r"""Chapter 40, Section 8 — State serialization.

Theory (theory2.tex §40.8):
    State serialization is the process of converting a SemanticState to a
    persistent or transmittable byte representation. Serialization must preserve
    all state invariants: section assignments, trust tier, obstruction records,
    generation round, and metadata.

    Three serialization formats are supported:
      JSON:    human-readable, universally compatible, largest footprint
      MSGPACK: binary, compact, ~3-5x smaller than JSON, fast encode/decode
      PICKLE:  Python-native, supports arbitrary objects, not cross-language

    Checkpointing records a snapshot of the search state at a given round,
    enabling resumption after failures. A CheckpointRecord stores the serialized
    state, the round number, the format used, and a content hash for integrity.

    Roundtrip validity: serialize(deserialize(serialize(σ))) = serialize(σ).
    Compression ratio ρ = |compressed| / |uncompressed|; for typical states ρ ≈ 0.3.

# copilot: s08-state-serialization
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import pickle
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

try:
    import msgpack  # type: ignore[import]
    _MSGPACK_AVAILABLE = True
except ImportError:
    _MSGPACK_AVAILABLE = False

try:
    from jugeo.generation.state_space.models import (
        SemanticState,
        GenerationStateSpace,
        ConvergenceMetric,
        make_initial_state,
        make_goal_state,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False
    SemanticState = Any  # type: ignore[misc,assignment]
    GenerationStateSpace = Any
    ConvergenceMetric = Any

    def make_initial_state(patches): return None
    def make_goal_state(patches, assignments): return None


__all__ = [
    "SerializationFormat",
    "SerializationResult",
    "CheckpointRecord",
    "SerializationError",
    "StateSerializationCoordinator",
    "StateSerializationAnalyzer",
    "StateSerializationWitness",
    "serialize_state",
    "deserialize_state",
    "load_checkpoint",
    "save_checkpoint",
]

# ---------------------------------------------------------------------------
# Extension-to-format mapping used when auto-detecting serialization format
# from a file path during restore operations.
# ---------------------------------------------------------------------------
_EXT_FORMAT_MAP: Dict[str, "SerializationFormat"] = {}  # populated after class definition


class SerializationFormat(Enum):
    """Enumeration of supported serialization formats.

    Each member corresponds to a distinct byte-level representation strategy:

    JSON
        Human-readable text serialization.  The state dict is encoded to a
        UTF-8 JSON string.  This is the safest, most portable choice and the
        default for all public APIs.  Typical uncompressed footprint for a
        medium-sized state is 1–4 KB.

    MSGPACK
        Binary MessagePack serialization.  Semantically equivalent to JSON
        but encoded as a compact binary stream.  Achieves roughly 3-5× size
        reduction versus JSON while being faster to encode and decode.
        Requires the optional ``msgpack`` package; if that is not installed
        the implementation falls back transparently to JSON.

    PICKLE
        Python ``pickle`` protocol 5 serialization.  Supports arbitrary Python
        objects (sets, custom types, …) without any manual dict conversion.
        Not cross-language and not safe to unpickle from untrusted sources.
        Suitable for short-lived local checkpoints where speed matters more
        than portability.
    """

    JSON = auto()
    MSGPACK = auto()
    PICKLE = auto()


# Populate extension map now that SerializationFormat is defined.
_EXT_FORMAT_MAP = {
    ".json": SerializationFormat.JSON,
    ".msgpack": SerializationFormat.MSGPACK,
    ".pkl": SerializationFormat.PICKLE,
    ".pickle": SerializationFormat.PICKLE,
}


class SerializationError(Exception):
    """Raised when a serialization or deserialization operation fails.

    Wraps the original low-level exception and preserves the format name so
    callers can distinguish, e.g., a MSGPACK encoding error from a JSON parse
    error without inspecting the exception chain directly.

    Attributes
    ----------
    format_name:
        String name of the ``SerializationFormat`` that triggered the error
        (e.g. ``"JSON"``, ``"MSGPACK"``, ``"PICKLE"``).
    original_error:
        The underlying exception that caused the serialization to fail.
    """

    def __init__(self, format_name: str, original_error: Exception) -> None:
        self.format_name = format_name
        self.original_error = original_error
        super().__init__(str(self))

    def __str__(self) -> str:  # noqa: D105
        return (
            f"SerializationError[format={self.format_name}]: "
            f"{type(self.original_error).__name__}: {self.original_error}"
        )


@dataclass
class SerializationResult:
    """Value object returned by serialization operations.

    Captures all metrics produced during a single serialize call so that
    callers can log, audit, or feed them into downstream analysis without
    re-running serialization.

    Attributes
    ----------
    success:
        ``True`` if serialization completed without error.
    format_name:
        String name of the format used (matches ``SerializationFormat`` member
        names).
    serialized_size_bytes:
        Exact byte-length of the serialized output.
    original_size_estimate:
        Rough estimate of the in-memory footprint of the original state object,
        derived from ``len(json.dumps(state.to_dict()))`` when available.
    compression_ratio:
        ``serialized_size_bytes / original_size_estimate``.  Values < 1 indicate
        that the chosen format is more compact than the uncompressed JSON
        baseline.
    content_hash:
        Hex-encoded SHA-256 digest of the serialized bytes.  Used for integrity
        verification at checkpoint restore time.
    encoding_time_seconds:
        Wall-clock seconds consumed by the encode step, excluding I/O.
    state_id:
        The ``state_id`` of the serialized ``SemanticState``.
    error_message:
        Non-empty only when ``success`` is ``False``; contains a human-readable
        description of what went wrong.
    """

    success: bool
    format_name: str
    serialized_size_bytes: int
    original_size_estimate: int
    compression_ratio: float
    content_hash: str
    encoding_time_seconds: float
    state_id: str
    error_message: str = ""


@dataclass
class CheckpointRecord:
    """Persistent record of a serialized state checkpoint.

    A checkpoint is a snapshot of the search state written to a file at a
    specific generation round.  ``CheckpointRecord`` carries enough metadata
    to locate, verify, and restore that snapshot without loading it.

    Attributes
    ----------
    checkpoint_id:
        Unique identifier for this checkpoint (UUID4).
    state_id:
        The ``state_id`` of the checkpointed ``SemanticState``.
    round_number:
        The ``generation_round`` at the time of checkpointing.
    format_name:
        Name of the serialization format used to write the file.
    file_path:
        Absolute or relative path to the checkpoint file on disk.
    content_hash:
        SHA-256 hex digest of the file contents at write time.
    serialized_size_bytes:
        Number of bytes written to the file.
    created_at:
        Unix timestamp (float) of checkpoint creation.
    metadata:
        Arbitrary key-value pairs for application-specific annotations.
    """

    checkpoint_id: str
    state_id: str
    round_number: int
    format_name: str
    file_path: str
    content_hash: str
    serialized_size_bytes: int
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def verify_integrity(self) -> bool:
        """Re-hash the checkpoint file and compare to the stored content hash.

        Returns
        -------
        bool
            ``True`` if the file exists and its SHA-256 digest matches
            ``self.content_hash``; ``False`` otherwise (file missing, truncated,
            or corrupted).

        Notes
        -----
        The file is read in 64 KiB chunks to avoid loading very large
        checkpoints entirely into memory during verification.
        """
        path = Path(self.file_path)
        if not path.exists():
            logger.warning(
                "CheckpointRecord.verify_integrity: file not found: %s", path
            )
            return False

        sha = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                # Read in 64 KiB chunks to keep memory usage bounded.
                while chunk := fh.read(65536):
                    sha.update(chunk)
        except OSError as exc:
            logger.error(
                "CheckpointRecord.verify_integrity: I/O error reading %s: %s",
                path,
                exc,
            )
            return False

        computed = sha.hexdigest()
        if computed != self.content_hash:
            logger.error(
                "CheckpointRecord.verify_integrity: hash mismatch for %s "
                "(expected %s, got %s)",
                path,
                self.content_hash,
                computed,
            )
            return False

        return True


def _sha256_hex(data: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def _state_to_dict(state: Any) -> Dict[str, Any]:
    """Coerce *state* to a plain Python dict suitable for JSON/MSGPACK encoding.

    Attempts ``state.to_dict()`` first (the canonical ``SemanticState`` API).
    Falls back to ``vars(state)`` for arbitrary objects.  If the result contains
    Python ``set`` objects they are converted to sorted lists so that JSON can
    encode them deterministically.
    """
    if hasattr(state, "to_dict") and callable(state.to_dict):
        raw = state.to_dict()
    elif hasattr(state, "__dict__"):
        raw = dict(vars(state))
    else:
        # Last resort: treat the object itself as the dict if it already is one.
        raw = dict(state) if isinstance(state, dict) else {"value": repr(state)}

    # JSON/MSGPACK cannot encode Python sets; convert to sorted lists so that
    # repeated serialization produces identical byte strings (determinism).
    return _coerce_sets(raw)


def _coerce_sets(obj: Any) -> Any:
    """Recursively replace ``set`` instances with sorted ``list`` instances.

    This is necessary because JSON and MSGPACK do not have a native set type.
    Using sorted lists guarantees deterministic output regardless of insertion
    order, which is important for content-hash stability.
    """
    if isinstance(obj, set):
        return sorted(_coerce_sets(v) for v in obj)
    if isinstance(obj, dict):
        return {k: _coerce_sets(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_coerce_sets(v) for v in obj]
    return obj


def _estimate_json_size(state: Any) -> int:
    """Estimate the JSON-encoded byte size of *state* without allocating the result.

    Used as the 'original_size_estimate' baseline in ``SerializationResult``.
    Returns 0 if estimation fails for any reason so that callers always get a
    non-exceptional result.
    """
    try:
        d = _state_to_dict(state)
        return len(json.dumps(d, separators=(",", ":")))
    except Exception:
        return 0


class StateSerializationCoordinator:
    """Orchestrates serialization, deserialization, checkpointing, and batch I/O.

    ``StateSerializationCoordinator`` is the primary entry-point for all
    serialization operations.  It maintains an in-process registry of every
    ``CheckpointRecord`` produced during its lifetime, enabling quick lookup
    without re-reading files.

    Design notes
    ------------
    * Format selection is explicit; there is no global default that changes
      behaviour across call sites.
    * MSGPACK availability is checked at call time, not at import time, so that
      late installation of the package is handled correctly.
    * All file I/O uses binary mode (``"wb"`` / ``"rb"``) regardless of format
      so that line-ending translation on Windows does not corrupt binary data.
    * The checkpoint registry is not persisted across process restarts; callers
      that need durability should store the ``CheckpointRecord`` themselves.
    """

    def __init__(self) -> None:
        # Maps checkpoint_id → CheckpointRecord for every checkpoint produced
        # by this coordinator instance during the current process lifetime.
        self._checkpoint_registry: Dict[str, CheckpointRecord] = {}

    # ------------------------------------------------------------------
    # Core encode / decode
    # ------------------------------------------------------------------

    def serialize(
        self,
        state: Any,
        fmt: SerializationFormat = SerializationFormat.JSON,
    ) -> bytes:
        """Convert *state* to a byte string using the specified format.

        Parameters
        ----------
        state:
            A ``SemanticState`` instance or any object that exposes a
            ``to_dict()`` method or ``__dict__``.
        fmt:
            The target serialization format.  Defaults to JSON.

        Returns
        -------
        bytes
            The serialized representation of *state*.

        Raises
        ------
        SerializationError
            If encoding fails for any reason, wrapping the original exception.

        Implementation notes
        --------------------
        JSON path
            ``state.to_dict()`` is called (or ``vars(state)`` as fallback),
            sets are coerced to sorted lists, and the result is encoded as a
            compact UTF-8 JSON string (no extra whitespace).

        MSGPACK path
            If ``msgpack`` is installed the dict is packed with
            ``use_bin_type=True`` (so Python ``str`` maps to msgpack ``str``,
            not ``bin``).  If ``msgpack`` is not installed this path silently
            falls back to the JSON path so that callers never see an import
            error at runtime.

        PICKLE path
            ``pickle.dumps`` with the highest available protocol is used.  No
            dict coercion is performed; the full Python object graph is
            serialized including ``set`` instances and custom types.
        """
        try:
            if fmt is SerializationFormat.JSON:
                d = _state_to_dict(state)
                # separators=(",", ":") produces the most compact valid JSON.
                return json.dumps(d, separators=(",", ":")).encode("utf-8")

            if fmt is SerializationFormat.MSGPACK:
                if not _MSGPACK_AVAILABLE:
                    # Graceful degradation: fall back to JSON encoding so that
                    # callers working in environments without msgpack still get
                    # a working result.
                    logger.debug(
                        "msgpack not available; falling back to JSON for MSGPACK request"
                    )
                    d = _state_to_dict(state)
                    return json.dumps(d, separators=(",", ":")).encode("utf-8")
                d = _state_to_dict(state)
                return msgpack.packb(d, use_bin_type=True)

            if fmt is SerializationFormat.PICKLE:
                # Use the highest protocol available in the running Python
                # version for maximum efficiency and feature support.
                return pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)

            # Should be unreachable given the Enum definition, but guards
            # against future additions that are not yet handled.
            raise ValueError(f"Unknown SerializationFormat: {fmt!r}")

        except SerializationError:
            raise
        except Exception as exc:
            raise SerializationError(fmt.name, exc) from exc

    def deserialize(
        self,
        data: bytes,
        fmt: SerializationFormat = SerializationFormat.JSON,
    ) -> Any:
        """Reconstruct a state object from serialized bytes.

        Parameters
        ----------
        data:
            Raw bytes produced by a previous call to :meth:`serialize`.
        fmt:
            The serialization format that was used to produce *data*.

        Returns
        -------
        Any
            A ``SemanticState`` instance if ``SemanticState.from_dict`` is
            available and the deserialized dict passes validation; otherwise a
            plain Python dict (for JSON/MSGPACK) or the raw pickled object (for
            PICKLE).

        Raises
        ------
        SerializationError
            On any decoding failure, wrapping the original exception.

        Implementation notes
        --------------------
        JSON / MSGPACK paths both produce a dict; the function attempts
        ``SemanticState.from_dict(d)`` if the models module is available, giving
        back a fully typed object.  If models are not available (stub mode) the
        raw dict is returned so that downstream code can still inspect the data.

        PICKLE path bypasses dict reconstruction entirely, returning whatever
        object was originally pickled.
        """
        try:
            if fmt is SerializationFormat.JSON:
                d = json.loads(data.decode("utf-8"))
                return self._dict_to_state(d)

            if fmt is SerializationFormat.MSGPACK:
                if not _MSGPACK_AVAILABLE:
                    # If we fell back to JSON during serialization we must also
                    # fall back to JSON during deserialization.
                    logger.debug(
                        "msgpack not available; deserializing MSGPACK data as JSON"
                    )
                    d = json.loads(data.decode("utf-8"))
                    return self._dict_to_state(d)
                d = msgpack.unpackb(data, raw=False)
                return self._dict_to_state(d)

            if fmt is SerializationFormat.PICKLE:
                return pickle.loads(data)  # noqa: S301

            raise ValueError(f"Unknown SerializationFormat: {fmt!r}")

        except SerializationError:
            raise
        except Exception as exc:
            raise SerializationError(fmt.name, exc) from exc

    def _dict_to_state(self, d: Dict[str, Any]) -> Any:
        """Convert a plain dict to a ``SemanticState`` if the models are available.

        If ``SemanticState.from_dict`` is importable and *d* contains the
        expected keys, a typed ``SemanticState`` is returned.  Otherwise the
        raw dict is returned unchanged, allowing the rest of the pipeline to
        work in degraded (stub) mode without crashing.

        Notes
        -----
        ``from_dict`` is expected to handle type coercion itself (e.g. turning
        a JSON list back into a Python ``set`` for ``obligations_open``).
        """
        if _MODELS_AVAILABLE and hasattr(SemanticState, "from_dict"):
            try:
                return SemanticState.from_dict(d)
            except Exception as exc:
                logger.debug(
                    "_dict_to_state: SemanticState.from_dict failed (%s); returning raw dict",
                    exc,
                )
        return d

    # ------------------------------------------------------------------
    # Checkpoint persistence
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        state: Any,
        path: Union[str, Path],
        fmt: SerializationFormat = SerializationFormat.JSON,
    ) -> CheckpointRecord:
        """Serialize *state* and write it to *path*, returning a ``CheckpointRecord``.

        The parent directory of *path* is created if it does not exist.  An
        existing file at *path* is silently overwritten.

        Parameters
        ----------
        state:
            The ``SemanticState`` (or compatible object) to checkpoint.
        path:
            Destination file path.  If no extension is present, one is
            appended based on *fmt* (``.json``, ``.msgpack``, ``.pkl``).
        fmt:
            Serialization format to use.

        Returns
        -------
        CheckpointRecord
            A fully populated record including ``content_hash`` and
            ``serialized_size_bytes``.  The record is also stored in
            ``_checkpoint_registry`` keyed by ``checkpoint_id``.

        Raises
        ------
        SerializationError
            If serialization fails.
        OSError
            If the file cannot be written (permissions, disk full, …).
        """
        path = Path(path)

        # Append a canonical extension if the caller did not supply one.
        _fmt_ext = {
            SerializationFormat.JSON: ".json",
            SerializationFormat.MSGPACK: ".msgpack",
            SerializationFormat.PICKLE: ".pkl",
        }
        if path.suffix == "":
            path = path.with_suffix(_fmt_ext[fmt])

        # Ensure the destination directory exists.
        path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize and compute integrity hash before touching the file system
        # so that a failed serialization leaves no partial file.
        data = self.serialize(state, fmt)
        content_hash = _sha256_hex(data)
        size_bytes = len(data)

        # Write atomically via a sibling temp file to avoid partial writes.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_bytes(data)
            tmp_path.replace(path)  # atomic on POSIX; best-effort on Windows
        except OSError:
            # Clean up temp file if the rename step failed.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        # Derive state_id and round_number from the state if possible.
        state_id = getattr(state, "state_id", str(uuid.uuid4()))
        round_number = getattr(state, "generation_round", 0)

        record = CheckpointRecord(
            checkpoint_id=str(uuid.uuid4()),
            state_id=state_id,
            round_number=round_number,
            format_name=fmt.name,
            file_path=str(path.resolve()),
            content_hash=content_hash,
            serialized_size_bytes=size_bytes,
        )
        self._checkpoint_registry[record.checkpoint_id] = record
        logger.info(
            "Checkpointed state %s (round %d) to %s [%s, %d bytes, sha256=%s]",
            state_id,
            round_number,
            path,
            fmt.name,
            size_bytes,
            content_hash[:16] + "…",
        )
        return record

    def restore(self, path: Union[str, Path]) -> Any:
        """Load and deserialize a checkpoint file, auto-detecting the format.

        Format detection strategy (in order):
        1. File extension (``.json`` → JSON, ``.msgpack`` → MSGPACK,
           ``.pkl`` / ``.pickle`` → PICKLE).
        2. If the extension is unrecognised, try JSON, then MSGPACK, then
           PICKLE, returning the first successful deserialization.

        Parameters
        ----------
        path:
            Path to the checkpoint file.

        Returns
        -------
        Any
            Deserialized state object.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        SerializationError
            If all format attempts fail.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint file not found: {path}")

        data = path.read_bytes()

        # Attempt format detection from extension first.
        detected_fmt = _EXT_FORMAT_MAP.get(path.suffix.lower())
        if detected_fmt is not None:
            return self.deserialize(data, detected_fmt)

        # Fallback: try each format in preference order.
        last_exc: Optional[Exception] = None
        for fmt in (SerializationFormat.JSON, SerializationFormat.MSGPACK, SerializationFormat.PICKLE):
            try:
                return self.deserialize(data, fmt)
            except SerializationError as exc:
                last_exc = exc

        raise SerializationError(
            "UNKNOWN",
            last_exc or RuntimeError("All deserialization attempts failed"),
        )

    def serialize_batch(
        self,
        states: List[Any],
        path: Union[str, Path],
        fmt: SerializationFormat = SerializationFormat.JSON,
    ) -> None:
        """Serialize a list of states and write them all to a single file.

        For JSON format the output is a JSON array where each element is the
        dict representation of one state.  For MSGPACK the output is a msgpack
        array.  For PICKLE the output is a pickled list.

        Parameters
        ----------
        states:
            Ordered list of state objects to serialize.
        path:
            Destination file path.
        fmt:
            Serialization format to use.

        Notes
        -----
        The entire batch is serialized into an in-memory buffer before writing
        so that a failed serialization does not produce a partial file.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if fmt is SerializationFormat.JSON:
            batch_list = [_state_to_dict(s) for s in states]
            data = json.dumps(batch_list, separators=(",", ":")).encode("utf-8")

        elif fmt is SerializationFormat.MSGPACK:
            if _MSGPACK_AVAILABLE:
                batch_list = [_state_to_dict(s) for s in states]
                data = msgpack.packb(batch_list, use_bin_type=True)
            else:
                # Fallback to JSON when msgpack is unavailable.
                batch_list = [_state_to_dict(s) for s in states]
                data = json.dumps(batch_list, separators=(",", ":")).encode("utf-8")

        elif fmt is SerializationFormat.PICKLE:
            data = pickle.dumps(states, protocol=pickle.HIGHEST_PROTOCOL)

        else:
            raise SerializationError(
                str(fmt),
                ValueError(f"Unknown SerializationFormat: {fmt!r}"),
            )

        path.write_bytes(data)
        logger.info(
            "Wrote batch of %d states to %s (%d bytes)",
            len(states),
            path,
            len(data),
        )

    # ------------------------------------------------------------------
    # Registry accessors
    # ------------------------------------------------------------------

    def get_checkpoint_record(self, checkpoint_id: str) -> Optional[CheckpointRecord]:
        """Return the ``CheckpointRecord`` for *checkpoint_id*, or ``None``."""
        return self._checkpoint_registry.get(checkpoint_id)

    def list_checkpoints(self) -> List[CheckpointRecord]:
        """Return all ``CheckpointRecord`` objects in creation order.

        The list is a snapshot; modifications to it do not affect the registry.
        """
        return list(self._checkpoint_registry.values())


class StateSerializationAnalyzer:
    """Provides measurement, validation, and benchmarking utilities.

    ``StateSerializationAnalyzer`` is a companion to
    ``StateSerializationCoordinator`` focused on analysis rather than I/O.  It
    uses a private ``StateSerializationCoordinator`` instance internally so that
    analysis operations never pollute the application-level checkpoint registry.

    All methods are pure in the sense that they do not write to the file system
    (though they may allocate significant temporary memory for large states).
    """

    def __init__(self) -> None:
        # Private coordinator used exclusively for encoding; its checkpoint
        # registry is never exposed to callers.
        self._coordinator = StateSerializationCoordinator()

    def compute_serialized_size(
        self,
        state: Any,
        fmt: SerializationFormat = SerializationFormat.JSON,
    ) -> int:
        """Return the exact byte-length of *state* when serialized with *fmt*.

        Parameters
        ----------
        state:
            State object to measure.
        fmt:
            Target format.

        Returns
        -------
        int
            Number of bytes in the serialized output.

        Notes
        -----
        This allocates the full serialized representation in memory.  For very
        large states consider calling with ``SerializationFormat.JSON`` first
        (cheapest) and using the result to gauge whether full benchmarking is
        worthwhile.
        """
        data = self._coordinator.serialize(state, fmt)
        return len(data)

    def estimate_compression_ratio(self, state: Any) -> float:
        """Estimate the MSGPACK-to-JSON size ratio for *state*.

        A ratio of 1.0 means MSGPACK and JSON are the same size; lower values
        mean MSGPACK is more compact (which is the typical case).

        Returns
        -------
        float
            Ratio in the range (0, ∞).  Values well above 1 indicate that
            MSGPACK overhead dominates, which can happen for very small states
            dominated by short string keys.

        Notes
        -----
        If ``msgpack`` is not installed the function returns the theoretically
        motivated default estimate of 0.35 (derived from the §40.8 statement
        that ρ ≈ 0.3 for typical states, with a small upward adjustment for
        conservative planning).
        """
        json_size = self.compute_serialized_size(state, SerializationFormat.JSON)
        if json_size == 0:
            # Degenerate case; avoid division by zero.
            return 1.0

        if not _MSGPACK_AVAILABLE:
            # Theoretical estimate from §40.8.
            return 0.35

        msgpack_size = self.compute_serialized_size(state, SerializationFormat.MSGPACK)
        return msgpack_size / json_size

    def validate_roundtrip(
        self,
        state: Any,
        fmt: SerializationFormat = SerializationFormat.JSON,
    ) -> bool:
        """Check that serializing then deserializing *state* recovers the original.

        Roundtrip validity is defined as:
            deserialize(serialize(σ)).state_id == σ.state_id
            deserialize(serialize(σ)).patch_assignments == σ.patch_assignments

        If the deserialized result is a plain dict (stub mode) the comparison
        falls back to dict key equality for the ``state_id`` key.

        Parameters
        ----------
        state:
            State to validate.
        fmt:
            Format to use for the roundtrip.

        Returns
        -------
        bool
            ``True`` if the roundtrip preserves the key invariants.
        """
        try:
            data = self._coordinator.serialize(state, fmt)
            recovered = self._coordinator.deserialize(data, fmt)
        except SerializationError as exc:
            logger.warning("validate_roundtrip: serialization failed: %s", exc)
            return False

        # Compare state_id.
        original_id = getattr(state, "state_id", None) or (
            state.get("state_id") if isinstance(state, dict) else None
        )
        if original_id is not None:
            if isinstance(recovered, dict):
                recovered_id = recovered.get("state_id")
            else:
                recovered_id = getattr(recovered, "state_id", None)
            if original_id != recovered_id:
                logger.debug(
                    "validate_roundtrip: state_id mismatch: %r != %r",
                    original_id,
                    recovered_id,
                )
                return False

        # Compare patch_assignments.
        if hasattr(state, "patch_assignments"):
            original_pa = state.patch_assignments
        elif isinstance(state, dict):
            original_pa = state.get("patch_assignments", {})
        else:
            original_pa = None

        if original_pa is not None:
            if isinstance(recovered, dict):
                recovered_pa = recovered.get("patch_assignments", {})
            else:
                recovered_pa = getattr(recovered, "patch_assignments", {})
            if original_pa != recovered_pa:
                logger.debug(
                    "validate_roundtrip: patch_assignments mismatch"
                )
                return False

        return True

    def compare_serialized(self, data1: bytes, data2: bytes) -> bool:
        """Return ``True`` if *data1* and *data2* have identical content hashes.

        Uses SHA-256 so that two byte strings are considered equal iff they
        encode the same information (modulo hash collisions, which are
        negligible for our purposes).

        Parameters
        ----------
        data1, data2:
            Byte strings to compare.

        Returns
        -------
        bool
            ``True`` if SHA-256(data1) == SHA-256(data2).
        """
        return _sha256_hex(data1) == _sha256_hex(data2)

    def benchmark_formats(self, state: Any) -> Dict[str, Dict[str, Any]]:
        """Serialize *state* in all formats and return timing and size statistics.

        Parameters
        ----------
        state:
            State object to benchmark.

        Returns
        -------
        Dict[str, Dict[str, Any]]
            Outer key is the format name (``"JSON"``, ``"MSGPACK"``,
            ``"PICKLE"``).  Inner dict contains:

            ``size_bytes``
                Exact serialized size in bytes.
            ``encode_time_seconds``
                Wall-clock seconds for the encode step.
            ``content_hash``
                SHA-256 hex digest of the serialized output.
            ``available``
                ``True`` if the format's optional dependency is present (always
                ``True`` for JSON and PICKLE; depends on ``msgpack`` install for
                MSGPACK).

        Notes
        -----
        Each format is benchmarked three times and the minimum time is reported
        to reduce noise from OS scheduling jitter.
        """
        results: Dict[str, Dict[str, Any]] = {}

        _format_availability = {
            SerializationFormat.JSON: True,
            SerializationFormat.MSGPACK: _MSGPACK_AVAILABLE,
            SerializationFormat.PICKLE: True,
        }

        for fmt in SerializationFormat:
            times: List[float] = []
            last_data: bytes = b""
            encode_ok = True

            # Run three trials; take the minimum encode time.
            for _ in range(3):
                t0 = time.perf_counter()
                try:
                    last_data = self._coordinator.serialize(state, fmt)
                    t1 = time.perf_counter()
                    times.append(t1 - t0)
                except SerializationError as exc:
                    logger.debug("benchmark_formats: %s failed: %s", fmt.name, exc)
                    encode_ok = False
                    break

            if encode_ok and last_data:
                results[fmt.name] = {
                    "size_bytes": len(last_data),
                    "encode_time_seconds": min(times),
                    "content_hash": _sha256_hex(last_data),
                    "available": _format_availability[fmt],
                }
            else:
                results[fmt.name] = {
                    "size_bytes": 0,
                    "encode_time_seconds": float("inf"),
                    "content_hash": "",
                    "available": _format_availability[fmt],
                }

        return results


@dataclass(frozen=True, slots=True)
class StateSerializationWitness:
    """Immutable proof-of-serialization record for auditing and logging.

    A ``StateSerializationWitness`` is created after a successful serialization
    and captures the key metrics in a frozen, hashable object that can be stored
    in sets, used as dict keys, or transmitted over a message queue without risk
    of mutation.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness (UUID4).
    state_id:
        The ``state_id`` of the witnessed ``SemanticState``.
    format_name:
        Name of the serialization format used.
    serialized_size_bytes:
        Exact byte-length of the serialized output.
    compression_ratio:
        ``serialized_size_bytes / original_size_estimate`` at the time of
        serialization.
    roundtrip_valid:
        Whether a roundtrip check was performed and passed.
    timestamp:
        Unix timestamp of witness creation.
    """

    witness_id: str
    state_id: str
    format_name: str
    serialized_size_bytes: int
    compression_ratio: float
    roundtrip_valid: bool
    timestamp: float

    @classmethod
    def from_result(
        cls,
        result: SerializationResult,
        roundtrip_valid: bool = True,
    ) -> "StateSerializationWitness":
        """Construct a ``StateSerializationWitness`` from a ``SerializationResult``.

        Parameters
        ----------
        result:
            The ``SerializationResult`` produced by a coordinator's serialize
            operation.  (Note: the coordinator's public API does not currently
            return ``SerializationResult`` directly; callers should build one
            from the data returned by :meth:`StateSerializationCoordinator.serialize`
            and :class:`StateSerializationAnalyzer`.)
        roundtrip_valid:
            Whether a roundtrip check was separately confirmed.  Defaults to
            ``True`` so that witnesses can be created without requiring an
            additional decode step in performance-sensitive code.

        Returns
        -------
        StateSerializationWitness
            A frozen witness instance.
        """
        return cls(
            witness_id=str(uuid.uuid4()),
            state_id=result.state_id,
            format_name=result.format_name,
            serialized_size_bytes=result.serialized_size_bytes,
            compression_ratio=result.compression_ratio,
            roundtrip_valid=roundtrip_valid,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Module-level convenience wrappers
# ---------------------------------------------------------------------------

# A single shared coordinator used by the module-level convenience functions.
# Applications that need separate checkpoint registries should instantiate
# their own ``StateSerializationCoordinator`` rather than using these wrappers.
_default_coordinator = StateSerializationCoordinator()


def serialize_state(
    state: Any,
    fmt: SerializationFormat = SerializationFormat.JSON,
) -> bytes:
    """Convenience wrapper: serialize *state* using the module-level coordinator.

    Parameters
    ----------
    state:
        State object to serialize.
    fmt:
        Target format.  Defaults to JSON.

    Returns
    -------
    bytes
        Serialized representation.

    See Also
    --------
    StateSerializationCoordinator.serialize : Full API with registry support.
    """
    return _default_coordinator.serialize(state, fmt)


def deserialize_state(
    data: bytes,
    fmt: SerializationFormat = SerializationFormat.JSON,
) -> Any:
    """Convenience wrapper: deserialize *data* using the module-level coordinator.

    Parameters
    ----------
    data:
        Bytes produced by :func:`serialize_state` (or any compatible encoder).
    fmt:
        Format that was used to produce *data*.

    Returns
    -------
    Any
        Deserialized state object (``SemanticState`` or dict).

    See Also
    --------
    StateSerializationCoordinator.deserialize : Full API with registry support.
    """
    return _default_coordinator.deserialize(data, fmt)


def save_checkpoint(
    state: Any,
    path: Union[str, Path],
    fmt: SerializationFormat = SerializationFormat.JSON,
) -> CheckpointRecord:
    """Convenience wrapper: checkpoint *state* using the module-level coordinator.

    Parameters
    ----------
    state:
        State object to checkpoint.
    path:
        Destination file path.
    fmt:
        Serialization format.

    Returns
    -------
    CheckpointRecord
        Record of the checkpoint that was written.

    See Also
    --------
    StateSerializationCoordinator.checkpoint : Full API with registry support.
    """
    return _default_coordinator.checkpoint(state, path, fmt)


def load_checkpoint(path: Union[str, Path]) -> Any:
    """Convenience wrapper: restore a checkpoint using the module-level coordinator.

    Parameters
    ----------
    path:
        Path to the checkpoint file.

    Returns
    -------
    Any
        Deserialized state object.

    See Also
    --------
    StateSerializationCoordinator.restore : Full API with format auto-detection.
    """
    return _default_coordinator.restore(path)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _build_test_state() -> Any:
    """Return a test state, using ``make_initial_state`` when models are available.

    When the models module is present a real ``SemanticState`` is constructed.
    Otherwise a plain dict that mirrors the ``SemanticState`` schema is returned
    so that the smoke test exercises the serialization code paths regardless of
    whether the full model stack is installed.
    """
    if _MODELS_AVAILABLE:
        patches = ["p-alpha", "p-beta", "p-gamma", "p-delta"]
        state = make_initial_state(patches)
        if state is not None:
            return state

    # Fallback stub state that mirrors SemanticState fields.
    return {
        "state_id": str(uuid.uuid4()),
        "patch_assignments": {
            "p-alpha": "section-1",
            "p-beta": "section-2",
            "p-gamma": "section-1",
            "p-delta": "section-3",
        },
        "obligations_open": ["obs-001", "obs-002"],
        "obligations_closed": ["obs-000"],
        "generation_round": 0,
        "is_terminal": False,
        "is_goal_state": False,
        "metadata": {
            "created_by": "state_serialization_smoke_test",
            "description": "Stub state used when models are not available",
        },
    }


def _run_smoke_test() -> None:
    """Execute a self-contained smoke test of the serialization pipeline.

    Test sequence
    -------------
    1.  Build a test state (real ``SemanticState`` if models available, else dict).
    2.  Serialize in all three formats; print sizes for comparison.
    3.  Validate roundtrip for each format.
    4.  Save and restore a JSON checkpoint; verify integrity.
    5.  Build a ``StateSerializationWitness`` for the JSON result.

    This function is intentionally verbose so that its output provides useful
    diagnostic information when run from the command line.
    """
    import tempfile

    print("=" * 70)
    print("state_serialization — smoke test")
    print("=" * 70)

    coordinator = StateSerializationCoordinator()
    analyzer = StateSerializationAnalyzer()

    # Step 1: Build test state.
    state = _build_test_state()
    state_id = getattr(state, "state_id", None) or (
        state.get("state_id") if isinstance(state, dict) else "unknown"
    )
    print(f"\n[1] Test state built.  state_id={state_id}")
    print(f"    Models available : {_MODELS_AVAILABLE}")
    print(f"    msgpack available: {_MSGPACK_AVAILABLE}")
    print(f"    State type       : {type(state).__name__}")

    # Step 2: Serialize in all formats and compare sizes.
    print("\n[2] Serialized sizes:")
    benchmark = analyzer.benchmark_formats(state)
    json_size = benchmark["JSON"]["size_bytes"]
    for fmt_name, metrics in benchmark.items():
        ratio = (
            metrics["size_bytes"] / json_size
            if json_size > 0 and metrics["size_bytes"] > 0
            else float("nan")
        )
        print(
            f"    {fmt_name:10s}: {metrics['size_bytes']:>8d} bytes  "
            f"encode={metrics['encode_time_seconds']*1000:.3f}ms  "
            f"ratio_vs_json={ratio:.3f}  "
            f"hash={metrics['content_hash'][:16]}…"
        )

    # Step 3: Validate roundtrip for each format.
    print("\n[3] Roundtrip validation:")
    roundtrip_results: Dict[str, bool] = {}
    for fmt in SerializationFormat:
        valid = analyzer.validate_roundtrip(state, fmt)
        roundtrip_results[fmt.name] = valid
        status = "PASS" if valid else "FAIL"
        print(f"    {fmt.name:10s}: {status}")

    # Step 4: Save and restore a checkpoint.
    print("\n[4] Checkpoint save/restore:")
    with tempfile.TemporaryDirectory() as tmpdir:
        ckpt_path = Path(tmpdir) / "test_checkpoint.json"
        record = coordinator.checkpoint(state, ckpt_path, SerializationFormat.JSON)
        print(f"    Written to      : {ckpt_path}")
        print(f"    checkpoint_id   : {record.checkpoint_id}")
        print(f"    content_hash    : {record.content_hash[:32]}…")
        print(f"    size_bytes      : {record.serialized_size_bytes}")

        integrity_ok = record.verify_integrity()
        print(f"    Integrity check : {'PASS' if integrity_ok else 'FAIL'}")

        restored = coordinator.restore(ckpt_path)
        restored_id = getattr(restored, "state_id", None) or (
            restored.get("state_id") if isinstance(restored, dict) else "unknown"
        )
        ids_match = restored_id == state_id
        print(f"    Restored state_id match: {'PASS' if ids_match else 'FAIL'}")

    # Step 5: Build a StateSerializationWitness.
    print("\n[5] StateSerializationWitness:")
    json_data = coordinator.serialize(state, SerializationFormat.JSON)
    json_size_bytes = len(json_data)
    est_original = _estimate_json_size(state) or json_size_bytes
    compression_ratio = json_size_bytes / est_original if est_original else 1.0

    result = SerializationResult(
        success=True,
        format_name="JSON",
        serialized_size_bytes=json_size_bytes,
        original_size_estimate=est_original,
        compression_ratio=compression_ratio,
        content_hash=_sha256_hex(json_data),
        encoding_time_seconds=benchmark["JSON"]["encode_time_seconds"],
        state_id=state_id,
    )
    witness = StateSerializationWitness.from_result(
        result, roundtrip_valid=roundtrip_results.get("JSON", False)
    )
    print(f"    witness_id       : {witness.witness_id}")
    print(f"    state_id         : {witness.state_id}")
    print(f"    format_name      : {witness.format_name}")
    print(f"    size_bytes       : {witness.serialized_size_bytes}")
    print(f"    compression_ratio: {witness.compression_ratio:.4f}")
    print(f"    roundtrip_valid  : {witness.roundtrip_valid}")

    print("\n" + "=" * 70)
    print("Smoke test complete.")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _run_smoke_test()
