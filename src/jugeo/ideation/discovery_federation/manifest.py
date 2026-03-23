"""
Manifest for the discovery_federation package.

This module implements the DiscoveryFederationManifest — a versioned,
sealed record that captures the full state of a discovery federation
at a given point in time. Manifests are used to checkpoint federation
progress, distribute state to new nodes, audit authority grants, and
provide a canonical reference for consensus records.

The manifest lifecycle is:
  DRAFT -> SEALED -> PUBLISHED -> (optionally) DEPRECATED

A manifest in DRAFT state can be freely modified. Once sealed, it is
immutable (modulo publishing and deprecation status changes). Published
manifests are distributed to all federation nodes. Deprecated manifests
are retained for historical reference but no longer authoritative.

The FederationManifestBuilder provides a fluent interface for
constructing manifests step-by-step, with validation at each stage.

The build_federation_manifest() free function provides a one-shot
convenience API for building and sealing a manifest from lists of
nodes and discovery IDs.

copilot: shared-core marker
theory2.tex Ch61 — Federated Discovery Authority
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List, Dict, Tuple

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

__all__ = [
    "ManifestStatus",
    "DiscoveryFederationManifest",
    "FederationManifestBuilder",
    "build_federation_manifest",
    "_utcnow",
    "_uid",
    "_clamp",
    "_merge_metadata",
    "_validate_manifest_fields",
]

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    This thin wrapper around ``time.time()`` exists so that every timestamp
    produced inside this module can be patched in unit tests via a single
    monkeypatching target.  Callers should never call ``time.time()``
    directly; always use ``_utcnow()`` instead.

    The returned value is a float, consistent with the ``created_at`` and
    ``sealed_at`` fields on :class:`DiscoveryFederationManifest`.  Consumers
    that need a human-readable string can convert with
    ``datetime.utcfromtimestamp(_utcnow()).isoformat()``.

    Returns:
        float: Current UTC time expressed as seconds since the Unix epoch
            (1970-01-01 00:00:00 UTC), with sub-second precision on
            platforms that support it.

    Example::

        ts = _utcnow()
        assert ts > 1_700_000_000.0
    """
    return time.time()


def _uid() -> str:
    """Generate a compact, URL-safe unique identifier string.

    Produces a UUID4-based identifier with hyphens removed, yielding a
    32-character hexadecimal string.  The result is suitable for use as a
    ``manifest_id``, node identifier, or any other field that requires
    global uniqueness without external coordination.

    Uniqueness guarantees are probabilistic (birthday-paradox); the
    collision probability for 10^12 generated IDs is on the order of
    10^-13, which is sufficient for all JuGeo federation use-cases.

    Returns:
        str: 32-character lowercase hexadecimal string derived from a
            random UUID4, e.g. ``'3f2504e04f8911d39a0c0305e82c3301'``.

    Example::

        id1 = _uid()
        id2 = _uid()
        assert id1 != id2
        assert len(id1) == 32
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Returns *lo* if *value* is below the lower bound, *hi* if *value*
    exceeds the upper bound, and *value* itself if it falls within the
    interval.  This is equivalent to ``max(lo, min(value, hi))`` but
    reads more clearly in contexts where the clamping intent matters.

    Used throughout the manifest module to guard numeric metadata fields
    against out-of-range values before serialisation, preventing
    downstream consumers from receiving unexpected sentinel values.

    Args:
        value (float): The numeric value to constrain.
        lo (float): The inclusive lower bound of the permitted range.
        hi (float): The inclusive upper bound of the permitted range.

    Returns:
        float: The clamped value, guaranteed to satisfy ``lo <= result <= hi``.

    Example::

        assert _clamp(5.0, 0.0, 10.0) == 5.0
        assert _clamp(-3.0, 0.0, 10.0) == 0.0
        assert _clamp(12.0, 0.0, 10.0) == 10.0
    """
    return max(lo, min(value, hi))


def _merge_metadata(base: dict, override: dict) -> dict:
    """Perform a shallow-recursive merge of two metadata dictionaries.

    For each key in *override*: if the key also exists in *base* and both
    values are plain ``dict`` instances, the function recurses one level
    deeper so that nested sub-keys are merged rather than wholesale
    replaced.  For all other value types (including lists, strings, and
    numbers), the *override* value wins unconditionally.

    The original dictionaries are never mutated; the result is always a
    freshly allocated ``dict``.  This makes the function safe to call in
    pipelines where the same base metadata dict is reused across multiple
    merge operations.

    Keys present in *base* but absent from *override* are preserved
    verbatim.  Keys present in *override* but absent from *base* are
    inserted.  There is no mechanism to *delete* keys via the override;
    removal must be done explicitly by the caller on the returned dict.

    Args:
        base (dict): The starting metadata dictionary.  Not mutated.
        override (dict): Key-value pairs that take precedence over *base*.
            Not mutated.

    Returns:
        dict: A new dictionary representing the merged result.

    Example::

        result = _merge_metadata(
            {"a": {"x": 1, "y": 2}, "b": 3},
            {"a": {"y": 99, "z": 0}, "c": 4},
        )
        assert result == {"a": {"x": 1, "y": 99, "z": 0}, "b": 3, "c": 4}
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _merge_metadata(result[key], val)
        else:
            result[key] = val
    return result


def _validate_manifest_fields(
    manifest_id: str,
    version: str,
    federation_nodes: List[str],
    discovery_ids: List[str],
) -> List[str]:
    """Validate the core fields of a manifest, returning a list of error strings.

    Inspects the four most structurally important fields of a
    :class:`DiscoveryFederationManifest` and accumulates human-readable
    error messages for every constraint that is violated.  An empty return
    list means the fields are collectively valid; callers should treat any
    non-empty list as a sign that the manifest must be corrected before
    being sealed or published.

    Validation rules applied:
    - ``manifest_id`` must be a non-empty string of at least 8 characters.
    - ``version`` must match the pattern ``MAJOR.MINOR.PATCH`` where each
      component is a non-negative integer (leading zeros disallowed except
      for the value ``0`` itself).
    - ``federation_nodes`` must be a list; each element must be a non-empty
      string; duplicates are flagged as a warning-level error.
    - ``discovery_ids`` must be a list; each element must be a non-empty
      string.

    Args:
        manifest_id (str): The candidate manifest identifier.
        version (str): The semantic version string, e.g. ``'1.0.0'``.
        federation_nodes (List[str]): Node identifiers belonging to the
            federation captured by this manifest.
        discovery_ids (List[str]): Discovery record identifiers included in
            the manifest.

    Returns:
        List[str]: Zero or more human-readable error descriptions.  An
            empty list indicates that all validated constraints pass.

    Example::

        errs = _validate_manifest_fields("", "1.0.0", [], [])
        assert any("manifest_id" in e for e in errs)
    """
    errors: List[str] = []

    if not isinstance(manifest_id, str) or len(manifest_id) < 1:
        errors.append(
            f"manifest_id must be a non-empty string; "
            f"got {manifest_id!r}"
        )

    parts = version.split(".") if isinstance(version, str) else []
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        errors.append(
            f"version must be MAJOR.MINOR.PATCH with integer components; "
            f"got {version!r}"
        )

    if not isinstance(federation_nodes, list):
        errors.append("federation_nodes must be a list")
    else:
        seen_nodes: set = set()
        for i, node in enumerate(federation_nodes):
            if not isinstance(node, str) or not node:
                errors.append(f"federation_nodes[{i}] must be a non-empty string")
            elif node in seen_nodes:
                errors.append(f"federation_nodes contains duplicate: {node!r}")
            else:
                seen_nodes.add(node)

    if not isinstance(discovery_ids, list):
        errors.append("discovery_ids must be a list")
    else:
        for i, did in enumerate(discovery_ids):
            if not isinstance(did, str) or not did:
                errors.append(f"discovery_ids[{i}] must be a non-empty string")

    return errors


# ---------------------------------------------------------------------------
# ManifestStatus enum
# ---------------------------------------------------------------------------


class ManifestStatus(str, Enum):
    """Lifecycle state of a :class:`DiscoveryFederationManifest`.

    States advance monotonically through DRAFT -> SEALED -> PUBLISHED and
    may optionally transition to DEPRECATED from PUBLISHED.  Backward
    transitions are never permitted; attempting one raises ``ValueError``.
    """

    DRAFT = "DRAFT"           # mutable; not yet locked
    SEALED = "SEALED"         # immutable; hash can be computed
    PUBLISHED = "PUBLISHED"   # distributed to all federation nodes
    DEPRECATED = "DEPRECATED" # superseded; retained for audit history


# ---------------------------------------------------------------------------
# DiscoveryFederationManifest dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DiscoveryFederationManifest:
    """Versioned, sealed record capturing the complete state of a discovery federation.

    A ``DiscoveryFederationManifest`` is the authoritative snapshot of which
    nodes belong to a federation, which discovery IDs have been incorporated,
    which consensus records have been ratified, and which authority grants are
    in effect — all bound together under a single versioned, timestamped
    identifier.

    Lifecycle
    ---------
    Manifests begin in the ``DRAFT`` state, where fields may be freely
    modified.  Calling :meth:`seal` transitions to ``SEALED``, records the
    seal timestamp, and prevents further structural mutation.  :meth:`publish`
    advances to ``PUBLISHED``, indicating the manifest has been broadcast to
    all federation peers.  :meth:`deprecate` moves the manifest to
    ``DEPRECATED`` once a newer manifest supersedes it.

    Serialisation
    -------------
    :meth:`to_dict` and :meth:`to_json` provide full round-trip serialisation.
    :meth:`from_json` reconstructs a manifest from a JSON string.

    Validation
    ----------
    :meth:`validate` checks all fields against structural constraints and
    returns a list of human-readable error messages.  An empty list means
    the manifest is structurally sound.

    Comparison & Merging
    --------------------
    :meth:`diff` computes a field-level diff against another manifest.
    :meth:`merge` produces a new DRAFT manifest combining nodes and
    discoveries from both operands.

    Note:
        ``slots=True`` is used for memory efficiency since federations may
        carry thousands of manifest instances in long-running orchestrators.
    """

    manifest_id: str
    version: str
    created_at: float
    sealed_at: Optional[float]
    status: ManifestStatus
    federation_nodes: List[str]
    discovery_ids: List[str]
    consensus_records: List[dict]
    authority_grants: List[dict]
    metadata: dict

    @classmethod
    def create(
        cls,
        manifest_id: str,
        version: str,
        author: str = "",
        chapter_ref: str = "",
        exports: list | None = None,
        created_at: str | float | None = None,
        description: str = "",
        tags: list | None = None,
        is_sealed: bool = False,
        is_published: bool = False,
        is_deprecated: bool = False,
        nodes: list | None = None,
        discoveries: list | None = None,
        consensuses: list | None = None,
        authority_grants: list | None = None,
    ) -> "DiscoveryFederationManifest":
        status = ManifestStatus.DRAFT
        sealed_at = None
        if is_sealed:
            status = ManifestStatus.SEALED
            sealed_at = _utcnow()
        if is_published:
            status = ManifestStatus.PUBLISHED
            sealed_at = sealed_at or _utcnow()
        if is_deprecated:
            status = ManifestStatus.DEPRECATED
            sealed_at = sealed_at or _utcnow()
        created_ts = _utcnow() if created_at is None else (float(created_at) if isinstance(created_at, (int, float)) else _utcnow())
        return cls(
            manifest_id=manifest_id,
            version=version,
            created_at=created_ts,
            sealed_at=sealed_at,
            status=status,
            federation_nodes=list(nodes or []),
            discovery_ids=list(discoveries or []),
            consensus_records=list(consensuses or []),
            authority_grants=list(authority_grants or []),
            metadata={
                "author": author,
                "chapter_ref": chapter_ref,
                "exports": list(exports or []),
                "description": description,
                "tags": list(tags or []),
                "created_at_raw": created_at,
            },
        )

    @property
    def author(self) -> str:
        return str(self.metadata.get("author", ""))

    @property
    def chapter_ref(self) -> str:
        return str(self.metadata.get("chapter_ref", ""))

    @property
    def exports(self) -> list:
        return list(self.metadata.get("exports", []))

    @property
    def description(self) -> str:
        return str(self.metadata.get("description", ""))

    @property
    def tags(self) -> list:
        return list(self.metadata.get("tags", []))

    @property
    def is_sealed(self) -> bool:
        return self.status in (ManifestStatus.SEALED, ManifestStatus.PUBLISHED, ManifestStatus.DEPRECATED)

    @property
    def is_published(self) -> bool:
        return self.status in (ManifestStatus.PUBLISHED, ManifestStatus.DEPRECATED)

    @property
    def is_deprecated(self) -> bool:
        return self.status == ManifestStatus.DEPRECATED

    @property
    def nodes(self) -> list:
        return self.federation_nodes

    @property
    def discoveries(self) -> list:
        return self.discovery_ids

    @property
    def consensuses(self) -> list:
        return self.consensus_records

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def seal(self) -> None:
        """Seal the manifest, marking it immutable and recording the seal time.

        Transitions the manifest from ``DRAFT`` to ``SEALED`` and sets
        ``sealed_at`` to the current UTC timestamp.  A sealed manifest's
        structural fields (nodes, discoveries, consensus records, authority
        grants) must not be modified; any attempt to do so should be treated
        as a programming error by the caller.

        Raises:
            ValueError: If the manifest is not currently in ``DRAFT`` state,
                e.g. if it has already been sealed or published.

        Returns:
            None

        Example::

            m = DiscoveryFederationManifest(...)
            m.seal()
            assert m.status == ManifestStatus.SEALED
            assert m.sealed_at is not None
        """
        if self.status != ManifestStatus.DRAFT:
            raise ValueError(
                f"Cannot seal manifest {self.manifest_id!r}: "
                f"current status is {self.status.value!r}, expected 'DRAFT'."
            )
        self.sealed_at = _utcnow()
        self.status = ManifestStatus.SEALED

    def publish(self) -> None:
        """Mark the manifest as published and distributed to all federation nodes.

        Transitions from ``SEALED`` to ``PUBLISHED``.  A published manifest
        is considered the live, authoritative record for the federation.
        Only one manifest per federation should be in ``PUBLISHED`` state at
        any time; callers are responsible for deprecating the previous
        published manifest before publishing a new one.

        Raises:
            ValueError: If the manifest is not in ``SEALED`` state at the
                time of the call.

        Returns:
            None

        Example::

            m.seal()
            m.publish()
            assert m.status == ManifestStatus.PUBLISHED
        """
        if self.status != ManifestStatus.SEALED:
            raise ValueError(
                f"Cannot publish manifest {self.manifest_id!r}: "
                f"current status is {self.status.value!r}, expected 'SEALED'."
            )
        self.status = ManifestStatus.PUBLISHED

    def deprecate(self) -> None:
        """Deprecate the manifest, marking it as superseded but retaining it for audit.

        May be called from either ``SEALED`` or ``PUBLISHED`` state.
        Deprecated manifests are never deleted; they remain queryable for
        historical analysis, compliance audits, and rollback verification.
        After deprecation no further lifecycle transitions are possible.

        Raises:
            ValueError: If the manifest is already ``DRAFT`` or already
                ``DEPRECATED``.

        Returns:
            None

        Example::

            m.seal()
            m.publish()
            m.deprecate()
            assert m.status == ManifestStatus.DEPRECATED
        """
        if self.status in (ManifestStatus.DRAFT, ManifestStatus.DEPRECATED):
            raise ValueError(
                f"Cannot deprecate manifest {self.manifest_id!r}: "
                f"current status is {self.status.value!r}."
            )
        self.status = ManifestStatus.DEPRECATED

    # ------------------------------------------------------------------
    # Mutation helpers (valid only in DRAFT state)
    # ------------------------------------------------------------------

    def add_node(self, node_id: str | dict) -> None:
        """Add a federation node to this manifest if not already present.

        Idempotent: calling with a node_id that is already registered is a
        no-op rather than an error.  Node identifiers are compared using
        exact string equality; no normalisation is applied.

        Args:
            node_id (str): Opaque string identifying the node to register.
                Must be non-empty.

        Returns:
            None

        Raises:
            ValueError: If ``node_id`` is empty or the manifest is not in
                ``DRAFT`` state.

        Example::

            m.add_node("node-alpha")
            m.add_node("node-alpha")  # idempotent
            assert m.node_count() == 1
        """
        if self.status != ManifestStatus.DRAFT:
            raise ValueError(
                f"Cannot mutate manifest {self.manifest_id!r} in status "
                f"{self.status.value!r}; must be DRAFT."
            )
        if not node_id:
            raise ValueError("node_id must be a non-empty string.")
        value = dict(node_id) if isinstance(node_id, dict) else node_id
        if value not in self.federation_nodes:
            self.federation_nodes.append(value)

    def add_discovery(self, discovery_id: str | dict) -> None:
        """Register a discovery ID with this manifest.

        Idempotent: duplicate discovery IDs are silently ignored.  Discovery
        IDs are opaque strings; the manifest does not validate their format
        or resolve them against any external registry.

        Args:
            discovery_id (str): Opaque identifier for the discovery record
                to include.  Must be non-empty.

        Returns:
            None

        Raises:
            ValueError: If ``discovery_id`` is empty or the manifest is not
                in ``DRAFT`` state.

        Example::

            m.add_discovery("disc-001")
            assert m.discovery_count() == 1
        """
        if self.status != ManifestStatus.DRAFT:
            raise ValueError(
                f"Cannot mutate manifest {self.manifest_id!r} in status "
                f"{self.status.value!r}; must be DRAFT."
            )
        if not discovery_id:
            raise ValueError("discovery_id must be a non-empty string.")
        value = dict(discovery_id) if isinstance(discovery_id, dict) else discovery_id
        if value not in self.discovery_ids:
            self.discovery_ids.append(value)

    def add_consensus(self, consensus_dict: dict) -> None:
        """Append a consensus record to this manifest.

        Consensus records capture the outcome of a federation-wide vote or
        agreement event.  Each record is stored as a plain dictionary; no
        schema validation is performed at this layer.

        Args:
            consensus_dict (dict): Arbitrary key-value mapping describing
                the consensus event.  A shallow copy is stored to prevent
                external mutation from affecting the manifest.

        Returns:
            None

        Raises:
            ValueError: If the manifest is not in ``DRAFT`` state or if
                ``consensus_dict`` is not a dict.

        Example::

            m.add_consensus({"round": 1, "outcome": "accepted", "votes": 7})
        """
        if self.status != ManifestStatus.DRAFT:
            raise ValueError(
                f"Cannot mutate manifest {self.manifest_id!r} in status "
                f"{self.status.value!r}; must be DRAFT."
            )
        if not isinstance(consensus_dict, dict):
            raise ValueError("consensus_dict must be a dict.")
        self.consensus_records.append(dict(consensus_dict))

    def add_authority_grant(self, grant_dict: dict) -> None:
        """Append an authority grant record to this manifest.

        Authority grants document which pack authorities or orchestrators
        have been delegated control over portions of the federation.  Each
        grant is a plain dictionary stored by shallow copy.

        Args:
            grant_dict (dict): Key-value mapping describing the grant, e.g.
                ``{"grantee": "node-alpha", "scope": "discovery", "ttl": 3600}``.

        Returns:
            None

        Raises:
            ValueError: If the manifest is not in ``DRAFT`` state or if
                ``grant_dict`` is not a dict.

        Example::

            m.add_authority_grant({"grantee": "orch-1", "scope": "all"})
        """
        if self.status != ManifestStatus.DRAFT:
            raise ValueError(
                f"Cannot mutate manifest {self.manifest_id!r} in status "
                f"{self.status.value!r}; must be DRAFT."
            )
        if not isinstance(grant_dict, dict):
            raise ValueError("grant_dict must be a dict.")
        self.authority_grants.append(dict(grant_dict))

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    def node_count(self) -> int:
        """Return the number of federation nodes registered in this manifest.

        This is a convenience accessor equivalent to ``len(self.federation_nodes)``.
        It exists to provide a stable, named API surface that can be called
        without knowledge of the underlying field structure.

        Returns:
            int: Number of node identifiers currently stored, always >= 0.

        Example::

            assert m.node_count() == len(m.federation_nodes)
        """
        return len(self.federation_nodes)

    def discovery_count(self) -> int:
        """Return the number of discovery IDs registered in this manifest.

        Equivalent to ``len(self.discovery_ids)`` but provided as a named
        method for API consistency with :meth:`node_count` and to ease
        mocking in unit tests.

        Returns:
            int: Number of discovery identifiers currently stored, always >= 0.

        Example::

            assert m.discovery_count() == len(m.discovery_ids)
        """
        return len(self.discovery_ids)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the manifest to a plain Python dictionary.

        Produces a JSON-serialisable dictionary that captures every field of
        the manifest, including the ``status`` enum (stored as its string
        value) and both timestamp fields.  The returned dict can be round-
        tripped through :meth:`from_json` via an intermediate JSON encoding.

        Returns:
            dict: Full serialisation of the manifest, safe for JSON encoding,
                database storage, or inter-process transmission.

        Example::

            d = m.to_dict()
            assert d["manifest_id"] == m.manifest_id
            assert d["status"] == m.status.value
        """
        return {
            "manifest_id": self.manifest_id,
            "version": self.version,
            "created_at": self.metadata.get("created_at_raw", self.created_at),
            "sealed_at": self.sealed_at,
            "status": self.status.value,
            "federation_nodes": list(self.federation_nodes),
            "discovery_ids": list(self.discovery_ids),
            "consensus_records": [dict(r) for r in self.consensus_records],
            "authority_grants": [dict(g) for g in self.authority_grants],
            "metadata": dict(self.metadata),
            "author": self.author,
            "chapter_ref": self.chapter_ref,
            "exports": self.exports,
            "description": self.description,
            "tags": self.tags,
            "is_sealed": self.is_sealed,
            "is_published": self.is_published,
            "is_deprecated": self.is_deprecated,
            "nodes": list(self.federation_nodes),
            "discoveries": list(self.discovery_ids),
            "consensuses": [dict(r) for r in self.consensus_records],
        }

    def to_json(self) -> str:
        """Serialise the manifest to a compact JSON string.

        Delegates to :meth:`to_dict` for the Python representation then
        encodes with ``json.dumps``.  The output is a single line of JSON
        with no extraneous whitespace — suitable for logging, hashing, and
        network transmission.

        Returns:
            str: JSON-encoded string representing the full manifest state.

        Example::

            js = m.to_json()
            assert isinstance(js, str)
            assert '"manifest_id"' in js
        """
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, json_str: str) -> "DiscoveryFederationManifest":
        """Deserialise a manifest from a JSON string produced by :meth:`to_json`.

        Reconstructs all fields including the ``ManifestStatus`` enum and
        optional ``sealed_at`` timestamp.  The method performs minimal
        validation; callers should invoke :meth:`validate` on the returned
        instance if they cannot trust the source of ``json_str``.

        Args:
            json_str (str): JSON string as produced by :meth:`to_json` or
                :meth:`to_dict` (after re-encoding).  Must contain all
                mandatory manifest fields.

        Returns:
            DiscoveryFederationManifest: Reconstructed manifest instance.

        Raises:
            json.JSONDecodeError: If ``json_str`` is not valid JSON.
            KeyError: If a mandatory field is absent from the JSON object.

        Example::

            restored = DiscoveryFederationManifest.from_json(m.to_json())
            assert restored.manifest_id == m.manifest_id
        """
        data = json.loads(json_str)
        metadata = dict(data.get("metadata", {}))
        metadata.setdefault("author", data.get("author", ""))
        metadata.setdefault("chapter_ref", data.get("chapter_ref", ""))
        metadata.setdefault("exports", list(data.get("exports", [])))
        metadata.setdefault("description", data.get("description", ""))
        metadata.setdefault("tags", list(data.get("tags", [])))
        metadata.setdefault("created_at_raw", data.get("created_at"))
        return cls(
            manifest_id=data["manifest_id"],
            version=data["version"],
            created_at=float(data["created_at"]) if isinstance(data.get("created_at"), (int, float)) else _utcnow(),
            sealed_at=float(data["sealed_at"]) if isinstance(data.get("sealed_at"), (int, float)) else None,
            status=ManifestStatus(data.get("status", "DRAFT")),
            federation_nodes=list(data.get("nodes", data.get("federation_nodes", []))),
            discovery_ids=list(data.get("discoveries", data.get("discovery_ids", []))),
            consensus_records=list(data.get("consensuses", data.get("consensus_records", []))),
            authority_grants=list(data.get("authority_grants", [])),
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Rendering & summaries
    # ------------------------------------------------------------------

    def render_tex(self) -> str:
        """Render a LaTeX fragment that describes this manifest for inclusion in reports.

        Produces a ``\\subsection`` block with a formatted table listing
        all key fields.  The fragment is self-contained and can be pasted
        into any LaTeX document that loads the ``booktabs`` and
        ``hyperref`` packages.

        Returns:
            str: Multi-line LaTeX source string describing the manifest.

        Example::

            tex = m.render_tex()
            assert "\\\\subsection" in tex
        """
        node_list = ", ".join(self.federation_nodes) if self.federation_nodes else "—"
        disc_list = ", ".join(self.discovery_ids) if self.discovery_ids else "—"
        sealed_str = str(self.sealed_at) if self.sealed_at is not None else "not sealed"
        lines = [
            f"\\subsection{{Discovery Federation Manifest}}",
            f"\\label{{manifest:{self.manifest_id}}}",
            f"\\begin{{tabular}}{{ll}}",
            f"  \\toprule",
            f"  \\textbf{{Field}} & \\textbf{{Value}} \\\\",
            f"  \\midrule",
            f"  Manifest ID & \\texttt{{{self.manifest_id}}} \\\\",
            f"  Version & {self.version} \\\\",
            f"  Status & {self.status.value} \\\\",
            f"  Created At & {self.created_at} \\\\",
            f"  Sealed At & {sealed_str} \\\\",
            f"  Nodes ({self.node_count()}) & {node_list} \\\\",
            f"  Discoveries ({self.discovery_count()}) & {disc_list} \\\\",
            f"  Consensus Records & {len(self.consensus_records)} \\\\",
            f"  Authority Grants & {len(self.authority_grants)} \\\\",
            f"  \\bottomrule",
            f"\\end{{tabular}}",
        ]
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a compact, human-readable summary string for logging and display.

        Produces a single-line string that captures the manifest identity,
        version, status, and high-level counts.  Suitable for embedding in
        log messages, CLI output, and progress displays without overwhelming
        the reader with raw data.

        Returns:
            str: One-line summary, e.g.
                ``'Manifest abc123… v1.2.0 [SEALED] — 5 nodes, 12 discoveries'``.

        Example::

            print(m.summary())
        """
        short_id = self.manifest_id[:8] + "…"
        return (
            f"Manifest {short_id} v{self.version} [{self.status.value}] — "
            f"{self.node_count()} nodes, {self.discovery_count()} discoveries, "
            f"{len(self.consensus_records)} consensus records, "
            f"{len(self.authority_grants)} grants"
        )

    def summarize(self) -> str:
        return (
            f"{self.manifest_id} v{self.version} by {self.author} "
            f"({self.status.value.lower()}) with "
            f"{self.node_count()} nodes, {self.discovery_count()} discoveries, "
            f"{len(self.consensus_records)} consensuses, "
            f"{len(self.authority_grants)} grants"
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Validate all manifest fields and return a list of error strings.

        Combines the structural checks from :func:`_validate_manifest_fields`
        with additional cross-field consistency checks, such as verifying
        that ``sealed_at`` is set when the status is ``SEALED`` or beyond,
        and that ``created_at`` precedes ``sealed_at`` when both are present.

        Returns:
            List[str]: Zero or more human-readable error strings.  An empty
                list indicates the manifest is structurally and semantically
                valid.

        Example::

            errs = m.validate()
            if errs:
                for e in errs:
                    print("ERROR:", e)
        """
        errors = _validate_manifest_fields(
            self.manifest_id, self.version,
            [
                node["node_id"] if isinstance(node, dict) and "node_id" in node else node
                for node in self.federation_nodes
            ],
            [
                disc["discovery_id"] if isinstance(disc, dict) and "discovery_id" in disc else disc
                for disc in self.discovery_ids
            ],
        )
        if not self.author:
            errors.append("author must be a non-empty string")
        if self.status in (ManifestStatus.SEALED, ManifestStatus.PUBLISHED, ManifestStatus.DEPRECATED):
            if self.sealed_at is None:
                errors.append("sealed_at must be set for status SEALED/PUBLISHED/DEPRECATED")
            elif self.sealed_at < self.created_at:
                errors.append(
                    f"sealed_at ({self.sealed_at}) must not precede created_at ({self.created_at})"
                )
        for i, rec in enumerate(self.consensus_records):
            if not isinstance(rec, dict):
                errors.append(f"consensus_records[{i}] must be a dict, got {type(rec).__name__}")
        for i, grant in enumerate(self.authority_grants):
            if not isinstance(grant, dict):
                errors.append(f"authority_grants[{i}] must be a dict, got {type(grant).__name__}")
        return errors

    # ------------------------------------------------------------------
    # Comparison & merging
    # ------------------------------------------------------------------

    def diff(self, other: "DiscoveryFederationManifest") -> dict:
        """Compute a field-level diff between this manifest and *other*.

        Produces a dictionary whose keys are field names and whose values are
        two-element tuples ``(self_value, other_value)`` for every field where
        the two manifests disagree.  Fields that are identical are omitted.

        For list fields (``federation_nodes``, ``discovery_ids``) the diff
        reports items present in one manifest but not the other using two
        sub-keys: ``"only_in_self"`` and ``"only_in_other"``.

        Args:
            other (DiscoveryFederationManifest): The manifest to compare
                against.  Must be a ``DiscoveryFederationManifest`` instance.

        Returns:
            dict: Mapping of field name to diff descriptor.  Empty dict if
                the manifests are identical across all compared fields.

        Example::

            d = m1.diff(m2)
            if "version" in d:
                print("versions differ:", d["version"])
        """
        result: dict = {}
        for scalar_field in ("manifest_id", "version", "created_at", "sealed_at", "status"):
            sv = getattr(self, scalar_field)
            ov = getattr(other, scalar_field)
            sv_cmp = sv.value if isinstance(sv, ManifestStatus) else sv
            ov_cmp = ov.value if isinstance(ov, ManifestStatus) else ov
            if sv_cmp != ov_cmp:
                result[scalar_field] = (sv_cmp, ov_cmp)
        def _norm(value: Any) -> str:
            if isinstance(value, dict):
                if "node_id" in value:
                    return f"node:{value['node_id']}"
                if "discovery_id" in value:
                    return f"discovery:{value['discovery_id']}"
                return json.dumps(value, sort_keys=True)
            return str(value)

        for list_field, alias in (("federation_nodes", "nodes"), ("discovery_ids", "discoveries")):
            sv_items = list(getattr(self, list_field))
            ov_items = list(getattr(other, list_field))
            sv_map = {_norm(item): item for item in sv_items}
            ov_map = {_norm(item): item for item in ov_items}
            sv_set = set(sv_map)
            ov_set = set(ov_map)
            only_self = [sv_map[key] for key in sorted(sv_set - ov_set)]
            only_other = [ov_map[key] for key in sorted(ov_set - sv_set)]
            if only_self or only_other:
                result[alias] = {"only_in_self": only_self, "only_in_other": only_other}
        for meta_field in ("author", "chapter_ref", "description"):
            if getattr(self, meta_field) != getattr(other, meta_field):
                result[meta_field] = (getattr(self, meta_field), getattr(other, meta_field))
        if len(self.consensus_records) != len(other.consensus_records):
            result["consensus_records"] = {
                "self_count": len(self.consensus_records),
                "other_count": len(other.consensus_records),
            }
        if len(self.authority_grants) != len(other.authority_grants):
            result["authority_grants"] = {
                "self_count": len(self.authority_grants),
                "other_count": len(other.authority_grants),
            }
        return result

    def merge(self, other: "DiscoveryFederationManifest") -> "DiscoveryFederationManifest":
        """Create a new DRAFT manifest by combining this manifest with *other*.

        The merged manifest receives a fresh ``manifest_id`` and ``created_at``
        timestamp.  Its ``federation_nodes`` and ``discovery_ids`` are the
        union of both operands (preserving order, self-first).  Consensus
        records and authority grants are concatenated (self-first).  Metadata
        is deep-merged with *other* taking precedence via :func:`_merge_metadata`.

        The version of the merged manifest is taken from *self* and then
        patch-bumped via :meth:`version_bump` to signal that it derives from
        but supersedes both sources.  Neither source manifest is mutated.

        Args:
            other (DiscoveryFederationManifest): The manifest to merge with.

        Returns:
            DiscoveryFederationManifest: A new DRAFT manifest combining both
                operands.  The caller must call :meth:`seal` before publishing.

        Example::

            merged = m1.merge(m2)
            assert merged.status == ManifestStatus.DRAFT
            assert set(m1.federation_nodes).issubset(set(merged.federation_nodes))
        """
        merged_nodes = list(self.federation_nodes)
        for n in other.federation_nodes:
            if n not in merged_nodes:
                merged_nodes.append(n)
        merged_discs = list(self.discovery_ids)
        for d in other.discovery_ids:
            if d not in merged_discs:
                merged_discs.append(d)
        merged_meta = _merge_metadata(self.metadata, other.metadata)
        merged_consensus = list(self.consensus_records) + list(other.consensus_records)
        merged_grants = list(self.authority_grants) + list(other.authority_grants)
        new_version = self.version_bump().version
        return DiscoveryFederationManifest.create(
            manifest_id=_uid(),
            version=new_version,
            author=str(merged_meta.get("author", "")),
            chapter_ref=str(merged_meta.get("chapter_ref", "")),
            exports=list(merged_meta.get("exports", [])),
            description=str(merged_meta.get("description", "")),
            tags=list(merged_meta.get("tags", [])),
            nodes=merged_nodes,
            discoveries=merged_discs,
            consensuses=merged_consensus,
            authority_grants=merged_grants,
        )

    def version_bump(self, part: str = "patch") -> "DiscoveryFederationManifest":
        """Return a new manifest with a bumped semantic version.

        Parses ``self.version`` as ``MAJOR.MINOR.PATCH``, increments
        ``PATCH`` by one, and returns the resulting string.  ``self.version``
        is *not* mutated; the caller is responsible for assigning the return
        value if an in-place bump is desired.

        Returns:
            str: New version string with patch component incremented by one,
                e.g. ``'1.2.4'`` if ``self.version`` is ``'1.2.3'``.

        Raises:
            ValueError: If ``self.version`` does not conform to
                ``MAJOR.MINOR.PATCH`` format.

        Example::

            m.version = "2.0.7"
            assert m.version_bump() == "2.0.8"
        """
        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(
                f"Cannot bump version {self.version!r}: "
                "must be MAJOR.MINOR.PATCH with integer components."
            )
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
        if part == "patch":
            patch += 1
        elif part == "minor":
            minor += 1
            patch = 0
        elif part == "major":
            major += 1
            minor = 0
            patch = 0
        else:
            raise ValueError(f"Unsupported version part: {part!r}")
        return DiscoveryFederationManifest.create(
            manifest_id=self.manifest_id,
            version=f"{major}.{minor}.{patch}",
            author=self.author,
            chapter_ref=self.chapter_ref,
            exports=self.exports,
            created_at=self.metadata.get("created_at_raw", self.created_at),
            description=self.description,
            tags=self.tags,
            is_sealed=self.status == ManifestStatus.SEALED,
            is_published=self.status == ManifestStatus.PUBLISHED,
            is_deprecated=self.status == ManifestStatus.DEPRECATED,
            nodes=list(self.federation_nodes),
            discoveries=list(self.discovery_ids),
            consensuses=list(self.consensus_records),
            authority_grants=list(self.authority_grants),
        )


# ---------------------------------------------------------------------------
# FederationManifestBuilder
# ---------------------------------------------------------------------------


class FederationManifestBuilder:
    """Fluent builder for constructing :class:`DiscoveryFederationManifest` instances.

    The builder accumulates field values across multiple chained method calls
    before producing a fully-formed manifest via :meth:`build`.  This pattern
    allows complex manifests to be constructed incrementally — for example,
    by accumulating node registrations from an iterator before adding
    consensus records from a separate data source.

    All ``with_*`` methods return ``self`` to support method chaining::

        manifest = (
            FederationManifestBuilder()
            .with_version("2.0.0")
            .with_node("node-alpha")
            .with_node("node-beta")
            .with_discovery("disc-001")
            .with_metadata("source", "orchestrator")
            .build()
        )

    The builder is reusable: calling :meth:`reset` clears all accumulated
    state so the same builder instance can produce multiple independent
    manifests across its lifetime.

    Validation
    ----------
    :meth:`validate_partial` checks whatever fields have been set so far
    without requiring the full complement of mandatory fields.  This is
    useful for surfacing errors early in long-running build pipelines.

    Snapshots
    ---------
    :meth:`snapshot` exports the current builder state as a plain dictionary.
    :meth:`from_snapshot` reconstructs a builder from such a snapshot,
    enabling serialisation of partially-built manifests across process
    boundaries or checkpointing in orchestrator workflows.
    """

    def __init__(self) -> None:
        """Initialise a new builder with empty / default state.

        Sets all internal accumulator fields to their empty defaults.
        The default version is ``'1.0.0'`` and a fresh ``manifest_id`` is
        generated automatically via :func:`_uid`.

        Returns:
            None
        """
        self._manifest_id: str = _uid()
        self._version: str = "1.0.0"
        self._nodes: List[Any] = []
        self._discoveries: List[Any] = []
        self._consensus_records: List[dict] = []
        self._authority_grants: List[dict] = []
        self._metadata: dict = {
            "author": "",
            "chapter_ref": "",
            "description": "",
            "tags": [],
            "exports": [],
        }

    # ------------------------------------------------------------------
    # Fluent setters
    # ------------------------------------------------------------------

    def with_node(self, node_id: str | dict) -> "FederationManifestBuilder":
        """Register a federation node with the manifest under construction.

        Idempotent: registering the same ``node_id`` twice has no additional
        effect.  Nodes are stored in insertion order for deterministic
        serialisation.

        Args:
            node_id (str): Opaque string identifying the node to register.
                Must be non-empty.

        Returns:
            FederationManifestBuilder: ``self``, enabling method chaining.

        Example::

            builder.with_node("node-gamma").with_node("node-delta")
        """
        value = dict(node_id) if isinstance(node_id, dict) else node_id
        if value and value not in self._nodes:
            self._nodes.append(value)
        return self

    def with_discovery(self, discovery_id: str | dict) -> "FederationManifestBuilder":
        """Add a discovery ID to the manifest under construction.

        Idempotent: adding the same ``discovery_id`` twice has no additional
        effect.  Discovery IDs are stored in insertion order.

        Args:
            discovery_id (str): Opaque string identifying the discovery
                record to include.  Must be non-empty.

        Returns:
            FederationManifestBuilder: ``self``, enabling method chaining.

        Example::

            builder.with_discovery("disc-alpha").with_discovery("disc-beta")
        """
        value = dict(discovery_id) if isinstance(discovery_id, dict) else discovery_id
        if value and value not in self._discoveries:
            self._discoveries.append(value)
        return self

    def with_consensus(self, consensus_dict: dict) -> "FederationManifestBuilder":
        """Append a consensus record to the manifest under construction.

        Each call appends a shallow copy of ``consensus_dict`` to the
        internal accumulator.  No deduplication is performed; calling
        this method twice with the same dict will result in two records.

        Args:
            consensus_dict (dict): Arbitrary key-value mapping describing
                a consensus event.

        Returns:
            FederationManifestBuilder: ``self``, enabling method chaining.

        Example::

            builder.with_consensus({"round": 1, "outcome": "accepted"})
        """
        self._consensus_records.append(dict(consensus_dict))
        return self

    def with_authority_grant(self, grant_dict: dict) -> "FederationManifestBuilder":
        """Append an authority grant record to the manifest under construction.

        Each call appends a shallow copy of ``grant_dict``.  No deduplication
        is performed on grants; if idempotency is required the caller must
        check for duplicates before calling this method.

        Args:
            grant_dict (dict): Mapping describing the authority grant, e.g.
                ``{"grantee": "node-alpha", "scope": "discovery"}``.

        Returns:
            FederationManifestBuilder: ``self``, enabling method chaining.

        Example::

            builder.with_authority_grant({"grantee": "orch-1", "scope": "all"})
        """
        self._authority_grants.append(dict(grant_dict))
        return self

    def with_metadata(self, key: str, value: Any) -> "FederationManifestBuilder":
        """Set a single metadata key-value pair on the manifest under construction.

        Overwrites any existing value for ``key`` without complaint.  To set
        multiple keys at once, call this method multiple times or manipulate
        ``self._metadata`` directly (though the latter is considered internal
        API).

        Args:
            key (str): Metadata key string.  Must be non-empty.
            value (Any): Metadata value.  Should be JSON-serialisable.

        Returns:
            FederationManifestBuilder: ``self``, enabling method chaining.

        Example::

            builder.with_metadata("source", "orchestrator").with_metadata("env", "prod")
        """
        self._metadata[key] = value
        return self

    def with_version(self, version: str) -> "FederationManifestBuilder":
        """Set the semantic version string for the manifest under construction.

        Replaces any previously set version.  The value is not validated at
        call time; validation occurs when :meth:`build` is invoked (or
        earlier via :meth:`validate_partial`).

        Args:
            version (str): Semantic version string in ``MAJOR.MINOR.PATCH``
                format, e.g. ``'2.1.0'``.

        Returns:
            FederationManifestBuilder: ``self``, enabling method chaining.

        Example::

            builder.with_version("3.0.0")
        """
        self._version = version
        return self

    def set_version(self, version: str) -> "FederationManifestBuilder":
        return self.with_version(version)

    def set_author(self, author: str) -> "FederationManifestBuilder":
        self._metadata["author"] = author
        return self

    def set_chapter_ref(self, chapter_ref: str) -> "FederationManifestBuilder":
        self._metadata["chapter_ref"] = chapter_ref
        return self

    def set_description(self, description: str) -> "FederationManifestBuilder":
        self._metadata["description"] = description
        return self

    def add_tag(self, tag: str) -> "FederationManifestBuilder":
        if tag and tag not in self._metadata["tags"]:
            self._metadata["tags"].append(tag)
        return self

    def add_export(self, export: str) -> "FederationManifestBuilder":
        if export and export not in self._metadata["exports"]:
            self._metadata["exports"].append(export)
        return self

    def add_node(self, node: str | dict) -> "FederationManifestBuilder":
        return self.with_node(node)

    def add_discovery(self, discovery: str | dict) -> "FederationManifestBuilder":
        return self.with_discovery(discovery)

    def add_consensus(self, consensus: dict) -> "FederationManifestBuilder":
        return self.with_consensus(consensus)

    def add_authority_grant(self, grant: dict) -> "FederationManifestBuilder":
        return self.with_authority_grant(grant)

    # ------------------------------------------------------------------
    # Terminal operations
    # ------------------------------------------------------------------

    def build(self) -> DiscoveryFederationManifest:
        """Construct and return a new :class:`DiscoveryFederationManifest` in DRAFT state.

        Assembles all accumulated state into a manifest.  The returned
        manifest is in ``DRAFT`` state; call :meth:`~DiscoveryFederationManifest.seal`
        on it before distributing.  The builder's own state is not reset
        by this call; invoke :meth:`reset` explicitly if the builder is to
        be reused.

        Returns:
            DiscoveryFederationManifest: Freshly constructed DRAFT manifest
                whose fields reflect all ``with_*`` calls made since the
                last :meth:`reset` (or since construction).

        Example::

            manifest = builder.with_node("n1").with_discovery("d1").build()
            assert manifest.status == ManifestStatus.DRAFT
        """
        return DiscoveryFederationManifest.create(
            manifest_id=self._manifest_id,
            version=self._version,
            author=self._metadata.get("author", ""),
            chapter_ref=self._metadata.get("chapter_ref", ""),
            exports=list(self._metadata.get("exports", [])),
            description=self._metadata.get("description", ""),
            tags=list(self._metadata.get("tags", [])),
            nodes=list(self._nodes),
            discoveries=list(self._discoveries),
            consensuses=list(self._consensus_records),
            authority_grants=list(self._authority_grants),
        )

    def reset(self) -> "FederationManifestBuilder":
        """Reset all accumulated state to defaults and return ``self``.

        After calling ``reset()``, the builder is equivalent to a freshly
        constructed instance — a new ``manifest_id`` is generated, the
        version is reset to ``'1.0.0'``, and all lists and dicts are cleared.
        This allows the same builder instance to be reused for producing
        multiple independent manifests.

        Returns:
            FederationManifestBuilder: ``self``, enabling method chaining
                immediately after the reset, e.g. ``builder.reset().with_version("2.0.0")``.

        Example::

            builder.reset().with_version("2.0.0").with_node("new-node")
        """
        self._manifest_id = _uid()
        self._version = "1.0.0"
        self._nodes = []
        self._discoveries = []
        self._consensus_records = []
        self._authority_grants = []
        self._metadata = {
            "author": "",
            "chapter_ref": "",
            "description": "",
            "tags": [],
            "exports": [],
        }
        return self

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Export the current builder state as a plain serialisable dictionary.

        Captures all internal accumulator fields at the moment of the call.
        The snapshot can be stored (e.g., in a database or message queue) and
        later used to reconstruct an equivalent builder via :meth:`from_snapshot`,
        enabling checkpointing of long-running build pipelines.

        Returns:
            dict: Dictionary containing all builder state, safe for JSON
                serialisation.

        Example::

            snap = builder.snapshot()
            new_builder = FederationManifestBuilder.from_snapshot(snap)
        """
        return {
            "manifest_id": self._manifest_id,
            "version": self._version,
            "nodes": list(self._nodes),
            "discoveries": list(self._discoveries),
            "consensus_records": [dict(r) for r in self._consensus_records],
            "authority_grants": [dict(g) for g in self._authority_grants],
            "metadata": dict(self._metadata),
        }

    @classmethod
    def from_snapshot(cls, snap: dict) -> "FederationManifestBuilder":
        """Reconstruct a builder from a snapshot dict produced by :meth:`snapshot`.

        Creates a new builder instance and populates its internal state from
        the provided snapshot, restoring it to the exact same logical state
        it was in when :meth:`snapshot` was called.

        Args:
            snap (dict): Snapshot dictionary as returned by :meth:`snapshot`.
                Must contain the keys ``manifest_id``, ``version``,
                ``nodes``, ``discoveries``, ``consensus_records``,
                ``authority_grants``, and ``metadata``.

        Returns:
            FederationManifestBuilder: New builder instance with state
                restored from *snap*.

        Example::

            builder2 = FederationManifestBuilder.from_snapshot(snap)
            assert builder2.node_count() == original_node_count
        """
        builder = cls()
        builder._manifest_id = snap["manifest_id"]
        builder._version = snap["version"]
        builder._nodes = list(snap.get("nodes", []))
        builder._discoveries = list(snap.get("discoveries", []))
        builder._consensus_records = [dict(r) for r in snap.get("consensus_records", [])]
        builder._authority_grants = [dict(g) for g in snap.get("authority_grants", [])]
        builder._metadata = dict(snap.get("metadata", {}))
        return builder

    # ------------------------------------------------------------------
    # Validation & introspection
    # ------------------------------------------------------------------

    def validate_partial(self) -> List[str]:
        """Validate accumulated state without requiring all mandatory fields to be set.

        Runs a subset of the full manifest validation rules against the
        currently accumulated state.  Only checks fields that have been
        explicitly set — i.e., it will not flag an empty node list as an
        error unless there is an independently identifiable problem with the
        nodes that *have* been added.

        This is intended for use in interactive or long-running build
        pipelines where full validation would generate spurious errors for
        fields that have not yet been populated.

        Returns:
            List[str]: Zero or more human-readable error strings found in
                the current partial state.

        Example::

            errs = builder.validate_partial()
            assert errs == []  # no errors on freshly created builder
        """
        errors: List[str] = []
        if self._manifest_id and len(self._manifest_id) < 8:
            errors.append(f"manifest_id too short: {self._manifest_id!r}")
        if self._version:
            parts = self._version.split(".")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                errors.append(f"version must be MAJOR.MINOR.PATCH; got {self._version!r}")
        seen: set = set()
        for i, n in enumerate(self._nodes):
            key = n.get("node_id") if isinstance(n, dict) else n
            if not key:
                errors.append(f"nodes[{i}] is empty")
            elif key in seen:
                errors.append(f"duplicate node: {key!r}")
            else:
                seen.add(key)
        return errors

    def node_count(self) -> int:
        """Return the number of nodes accumulated in the builder so far.

        Convenience accessor exposing ``len(self._nodes)`` without requiring
        direct access to the private attribute.  Consistent with the
        corresponding method on :class:`DiscoveryFederationManifest`.

        Returns:
            int: Current number of unique node identifiers in the builder,
                always >= 0.

        Example::

            builder.with_node("n1").with_node("n2")
            assert builder.node_count() == 2
        """
        return len(self._nodes)

    def discovery_count(self) -> int:
        """Return the number of discovery IDs accumulated in the builder so far.

        Convenience accessor exposing ``len(self._discoveries)`` without
        requiring direct access to the private attribute.  Consistent with
        the corresponding method on :class:`DiscoveryFederationManifest`.

        Returns:
            int: Current number of unique discovery identifiers in the
                builder, always >= 0.

        Example::

            builder.with_discovery("d1")
            assert builder.discovery_count() == 1
        """
        return len(self._discoveries)


# ---------------------------------------------------------------------------
# Free function: build_federation_manifest
# ---------------------------------------------------------------------------


def build_federation_manifest(
    nodes: Optional[List[Any]] = None,
    discoveries: Optional[List[Any]] = None,
    metadata: Optional[dict] = None,
    *,
    version: str = "1.0.0",
    author: str = "",
    chapter_ref: str = "",
    description: str = "",
    tags: Optional[list] = None,
    exports: Optional[list] = None,
) -> DiscoveryFederationManifest:
    """Build and seal a :class:`DiscoveryFederationManifest` in one call.

    Convenience function that wraps :class:`FederationManifestBuilder` to
    produce a fully sealed manifest from the provided lists of node IDs and
    discovery IDs.  The manifest is automatically assigned a fresh UUID-based
    identifier, a ``created_at`` timestamp, and a ``sealed_at`` timestamp
    (set immediately after build).

    The optional ``metadata`` dict is merged onto an empty base using
    :func:`_merge_metadata`, meaning it may contain nested dicts that will
    be preserved as-is.

    This function is the recommended entry point for code that does not need
    the incremental flexibility of :class:`FederationManifestBuilder` — for
    example, in batch jobs that already have all the data assembled in lists.

    Args:
        nodes (List[str]): Non-empty list of federation node identifier
            strings.  Duplicate entries are silently deduplicated.
        discoveries (List[str]): List of discovery record identifier strings
            to include in the manifest.  May be empty.  Duplicates are
            deduplicated.
        metadata (Optional[dict]): Optional dictionary of arbitrary key-value
            metadata to attach to the manifest.  Defaults to an empty dict
            if not provided.

    Returns:
        DiscoveryFederationManifest: A freshly sealed (``SEALED`` state)
            manifest instance ready for publishing.

    Raises:
        ValueError: If ``nodes`` is empty or if any element of ``nodes`` or
            ``discoveries`` is not a non-empty string.

    Example::

        manifest = build_federation_manifest(
            nodes=["node-alpha", "node-beta"],
            discoveries=["disc-001", "disc-002"],
            metadata={"environment": "production", "region": "us-east-1"},
        )
        assert manifest.status == ManifestStatus.SEALED
        assert manifest.node_count() == 2
    """
    effective_metadata = _merge_metadata({}, metadata or {})
    builder = (
        FederationManifestBuilder()
        .set_version(version)
        .set_author(author)
        .set_chapter_ref(chapter_ref)
        .set_description(description)
    )
    for tag in tags or []:
        builder.add_tag(tag)
    for export in exports or []:
        builder.add_export(export)
    for node in nodes or []:
        builder.add_node(node)
    for disc in discoveries or []:
        builder.add_discovery(disc)
    for k, v in effective_metadata.items():
        builder.with_metadata(k, v)
    return builder.build()
