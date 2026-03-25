from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    """Status of a verification task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EventKind(str, Enum):
    """Kinds of events emitted by the verification server."""

    JUDGMENT_ADDED = "JUDGMENT_ADDED"
    EVIDENCE_ADDED = "EVIDENCE_ADDED"
    OBSTRUCTION_FOUND = "OBSTRUCTION_FOUND"
    TRUST_CHANGED = "TRUST_CHANGED"
    PHASE_CHANGED = "PHASE_CHANGED"
    TASK_COMPLETED = "TASK_COMPLETED"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VerificationRequest:
    """A request to verify a target path at a given scope."""

    id: str
    target_path: str
    scope: str  # LOCAL, PACKAGE, PROJECT
    trust_target: str
    incremental: bool = True
    callback_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target_path": self.target_path,
            "scope": self.scope,
            "trust_target": self.trust_target,
            "incremental": self.incremental,
            "callback_url": self.callback_url,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerificationRequest:
        return cls(
            id=data["id"],
            target_path=data["target_path"],
            scope=data["scope"],
            trust_target=data["trust_target"],
            incremental=data.get("incremental", True),
            callback_url=data.get("callback_url"),
        )


@dataclass
class VerificationProgress:
    """Live progress report for a running verification task."""

    task_id: str
    status: TaskStatus
    progress_pct: float
    coordinates_verified: int
    total_coordinates: int
    obstructions_found: int
    current_phase: str
    started_at: float
    estimated_remaining_s: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "progress_pct": self.progress_pct,
            "coordinates_verified": self.coordinates_verified,
            "total_coordinates": self.total_coordinates,
            "obstructions_found": self.obstructions_found,
            "current_phase": self.current_phase,
            "started_at": self.started_at,
            "estimated_remaining_s": self.estimated_remaining_s,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerificationProgress:
        status_val = data["status"]
        if isinstance(status_val, str):
            status_val = TaskStatus(status_val)
        return cls(
            task_id=data["task_id"],
            status=status_val,
            progress_pct=data["progress_pct"],
            coordinates_verified=data["coordinates_verified"],
            total_coordinates=data["total_coordinates"],
            obstructions_found=data["obstructions_found"],
            current_phase=data["current_phase"],
            started_at=data["started_at"],
            estimated_remaining_s=data["estimated_remaining_s"],
        )


@dataclass
class VerificationResult:
    """Final result of a completed verification task."""

    task_id: str
    success: bool
    trust_achieved: str
    coverage: float
    obstructions: list
    certificate_id: Optional[str] = None
    duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "trust_achieved": self.trust_achieved,
            "coverage": self.coverage,
            "obstructions": self.obstructions,
            "certificate_id": self.certificate_id,
            "duration_s": self.duration_s,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerificationResult:
        return cls(
            task_id=data["task_id"],
            success=data["success"],
            trust_achieved=data["trust_achieved"],
            coverage=data["coverage"],
            obstructions=data.get("obstructions", []),
            certificate_id=data.get("certificate_id"),
            duration_s=data.get("duration_s", 0.0),
        )


@dataclass
class ServerConfig:
    """Configuration for the async verification server."""

    host: str = "0.0.0.0"
    port: int = 8765
    max_concurrent_tasks: int = 4
    task_timeout_s: float = 3600
    enable_websocket: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "task_timeout_s": self.task_timeout_s,
            "enable_websocket": self.enable_websocket,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ServerConfig:
        return cls(
            host=data.get("host", "0.0.0.0"),
            port=data.get("port", 8765),
            max_concurrent_tasks=data.get("max_concurrent_tasks", 4),
            task_timeout_s=data.get("task_timeout_s", 3600),
            enable_websocket=data.get("enable_websocket", True),
        )


@dataclass
class ClientSession:
    """Represents a connected client."""

    id: str
    workspace_id: str
    connected_at: float
    last_active_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "connected_at": self.connected_at,
            "last_active_at": self.last_active_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClientSession:
        return cls(
            id=data["id"],
            workspace_id=data["workspace_id"],
            connected_at=data["connected_at"],
            last_active_at=data["last_active_at"],
        )
