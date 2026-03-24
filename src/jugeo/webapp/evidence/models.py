"""Data models for multi-channel web evidence.

Defines the evidence types, trust levels, and supporting structures
used throughout the evidence subsystem.  Every dataclass provides
``to_dict`` / ``from_dict`` for JSON-friendly serialisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WebEvidenceChannel(str, Enum):
    """Channels through which evidence about a web application is gathered."""

    PYTHON_TYPE_CHECK = "python_type_check"
    TYPESCRIPT_COMPILE = "typescript_compile"
    SQL_SCHEMA_VALIDATE = "sql_schema_validate"
    JINJA2_LINT = "jinja2_lint"
    CSS_LINT = "css_lint"
    HTML_VALIDATE = "html_validate"
    API_CONTRACT_TEST = "api_contract_test"
    INTEGRATION_TEST = "integration_test"
    BROWSER_TEST = "browser_test"
    CROSS_LANGUAGE_STATIC = "cross_language_static"
    ACCESSIBILITY_AUDIT = "accessibility_audit"
    VISUAL_REGRESSION = "visual_regression"
    SECURITY_SCAN = "security_scan"


class WebTrustLevel(str, Enum):
    """Trust levels for web evidence, ordered from weakest to strongest.

    The ordering follows the *trust lattice* defined in §4.2 of the
    Geometry of Web Applications document.
    """

    USER_INPUT = "user_input"
    BROWSER_TESTED = "browser_tested"
    CSS_LINTED = "css_linted"
    CLIENT_VALIDATED = "client_validated"
    JS_TYPE_CHECKED = "js_type_checked"
    TEMPLATE_TYPE_CHECKED = "template_type_checked"
    SCHEMA_VALIDATED = "schema_validated"
    API_CONTRACT_TESTED = "api_contract_tested"
    ORM_TYPE_CHECKED = "orm_type_checked"
    MIDDLEWARE_ENFORCED = "middleware_enforced"
    SERVER_VALIDATED = "server_validated"
    DB_CONSTRAINT_ENFORCED = "db_constraint_enforced"
    SOLVER_DISCHARGED = "solver_discharged"
    MECHANICALLY_VERIFIED = "mechanically_verified"


# Canonical ascending ordering of trust levels (index 0 = weakest).
TRUST_ORDER: list[str] = [
    WebTrustLevel.USER_INPUT.value,
    WebTrustLevel.BROWSER_TESTED.value,
    WebTrustLevel.CSS_LINTED.value,
    WebTrustLevel.CLIENT_VALIDATED.value,
    WebTrustLevel.JS_TYPE_CHECKED.value,
    WebTrustLevel.TEMPLATE_TYPE_CHECKED.value,
    WebTrustLevel.SCHEMA_VALIDATED.value,
    WebTrustLevel.API_CONTRACT_TESTED.value,
    WebTrustLevel.ORM_TYPE_CHECKED.value,
    WebTrustLevel.MIDDLEWARE_ENFORCED.value,
    WebTrustLevel.SERVER_VALIDATED.value,
    WebTrustLevel.DB_CONSTRAINT_ENFORCED.value,
    WebTrustLevel.SOLVER_DISCHARGED.value,
    WebTrustLevel.MECHANICALLY_VERIFIED.value,
]


# ---------------------------------------------------------------------------
# Helper functions for trust ordering
# ---------------------------------------------------------------------------

def trust_level_index(trust: str) -> int:
    """Return the index of *trust* in ``TRUST_ORDER``.

    Returns ``-1`` when the value is not found.
    """
    try:
        return TRUST_ORDER.index(trust)
    except ValueError:
        return -1


def compare_trust(t1: str, t2: str) -> int:
    """Compare two trust-level strings.

    Returns:
        -1 if *t1* < *t2*, 0 if equal, 1 if *t1* > *t2*.
    """
    i1 = trust_level_index(t1)
    i2 = trust_level_index(t2)
    if i1 < i2:
        return -1
    if i1 > i2:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WebEvidence:
    """A single piece of evidence produced by one verification channel."""

    id: str
    channel: WebEvidenceChannel
    claim: str
    coordinate_id: str
    trust_level: WebTrustLevel
    timestamp: float
    details: dict = field(default_factory=dict)
    file_path: str = ""
    line_number: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel": self.channel.value,
            "claim": self.claim,
            "coordinate_id": self.coordinate_id,
            "trust_level": self.trust_level.value,
            "timestamp": self.timestamp,
            "details": dict(self.details),
            "file_path": self.file_path,
            "line_number": self.line_number,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WebEvidence:
        return cls(
            id=data["id"],
            channel=WebEvidenceChannel(data["channel"]),
            claim=data["claim"],
            coordinate_id=data["coordinate_id"],
            trust_level=WebTrustLevel(data["trust_level"]),
            timestamp=data["timestamp"],
            details=data.get("details", {}),
            file_path=data.get("file_path", ""),
            line_number=data.get("line_number", 0),
        )


@dataclass
class EvidenceBundle:
    """Evidence items grouped by coordinate, with combined trust metadata."""

    coordinate_id: str
    evidence_items: list[WebEvidence] = field(default_factory=list)
    combined_trust: str = ""
    convergence_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "coordinate_id": self.coordinate_id,
            "evidence_items": [e.to_dict() for e in self.evidence_items],
            "combined_trust": self.combined_trust,
            "convergence_score": self.convergence_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EvidenceBundle:
        items_raw = data.get("evidence_items", [])
        items = [WebEvidence.from_dict(d) for d in items_raw]
        return cls(
            coordinate_id=data["coordinate_id"],
            evidence_items=items,
            combined_trust=data.get("combined_trust", ""),
            convergence_score=data.get("convergence_score", 0.0),
        )


@dataclass
class ChannelCapability:
    """Describes what a single evidence channel can verify."""

    channel: WebEvidenceChannel
    languages_checked: list[str] = field(default_factory=list)
    trust_range: list[str] = field(default_factory=list)
    tooling: str = ""
    pixel_involvement: str = ""

    def to_dict(self) -> dict:
        return {
            "channel": self.channel.value,
            "languages_checked": list(self.languages_checked),
            "trust_range": list(self.trust_range),
            "tooling": self.tooling,
            "pixel_involvement": self.pixel_involvement,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChannelCapability:
        return cls(
            channel=WebEvidenceChannel(data["channel"]),
            languages_checked=data.get("languages_checked", []),
            trust_range=data.get("trust_range", []),
            tooling=data.get("tooling", ""),
            pixel_involvement=data.get("pixel_involvement", ""),
        )


# Mapping of each channel to its capability descriptor.
CHANNEL_CAPABILITIES: dict[WebEvidenceChannel, ChannelCapability] = {
    WebEvidenceChannel.PYTHON_TYPE_CHECK: ChannelCapability(
        channel=WebEvidenceChannel.PYTHON_TYPE_CHECK,
        languages_checked=["python"],
        trust_range=[
            WebTrustLevel.ORM_TYPE_CHECKED.value,
            WebTrustLevel.SERVER_VALIDATED.value,
        ],
        tooling="mypy / pyright",
        pixel_involvement="none",
    ),
    WebEvidenceChannel.TYPESCRIPT_COMPILE: ChannelCapability(
        channel=WebEvidenceChannel.TYPESCRIPT_COMPILE,
        languages_checked=["typescript", "javascript"],
        trust_range=[
            WebTrustLevel.JS_TYPE_CHECKED.value,
            WebTrustLevel.CLIENT_VALIDATED.value,
        ],
        tooling="tsc",
        pixel_involvement="none",
    ),
    WebEvidenceChannel.SQL_SCHEMA_VALIDATE: ChannelCapability(
        channel=WebEvidenceChannel.SQL_SCHEMA_VALIDATE,
        languages_checked=["sql"],
        trust_range=[
            WebTrustLevel.SCHEMA_VALIDATED.value,
            WebTrustLevel.DB_CONSTRAINT_ENFORCED.value,
        ],
        tooling="sqlfluff / schema linter",
        pixel_involvement="none",
    ),
    WebEvidenceChannel.JINJA2_LINT: ChannelCapability(
        channel=WebEvidenceChannel.JINJA2_LINT,
        languages_checked=["jinja2", "html"],
        trust_range=[
            WebTrustLevel.TEMPLATE_TYPE_CHECKED.value,
            WebTrustLevel.TEMPLATE_TYPE_CHECKED.value,
        ],
        tooling="jinja2-lint / djlint",
        pixel_involvement="none",
    ),
    WebEvidenceChannel.CSS_LINT: ChannelCapability(
        channel=WebEvidenceChannel.CSS_LINT,
        languages_checked=["css"],
        trust_range=[
            WebTrustLevel.CSS_LINTED.value,
            WebTrustLevel.CSS_LINTED.value,
        ],
        tooling="stylelint",
        pixel_involvement="indirect",
    ),
    WebEvidenceChannel.HTML_VALIDATE: ChannelCapability(
        channel=WebEvidenceChannel.HTML_VALIDATE,
        languages_checked=["html"],
        trust_range=[
            WebTrustLevel.CLIENT_VALIDATED.value,
            WebTrustLevel.CLIENT_VALIDATED.value,
        ],
        tooling="html5-parser / vnu",
        pixel_involvement="indirect",
    ),
    WebEvidenceChannel.API_CONTRACT_TEST: ChannelCapability(
        channel=WebEvidenceChannel.API_CONTRACT_TEST,
        languages_checked=["python", "javascript"],
        trust_range=[
            WebTrustLevel.API_CONTRACT_TESTED.value,
            WebTrustLevel.SERVER_VALIDATED.value,
        ],
        tooling="schemathesis / dredd",
        pixel_involvement="none",
    ),
    WebEvidenceChannel.INTEGRATION_TEST: ChannelCapability(
        channel=WebEvidenceChannel.INTEGRATION_TEST,
        languages_checked=["python", "javascript", "sql"],
        trust_range=[
            WebTrustLevel.API_CONTRACT_TESTED.value,
            WebTrustLevel.SERVER_VALIDATED.value,
        ],
        tooling="pytest / jest",
        pixel_involvement="none",
    ),
    WebEvidenceChannel.BROWSER_TEST: ChannelCapability(
        channel=WebEvidenceChannel.BROWSER_TEST,
        languages_checked=["javascript", "html", "css"],
        trust_range=[
            WebTrustLevel.BROWSER_TESTED.value,
            WebTrustLevel.CLIENT_VALIDATED.value,
        ],
        tooling="playwright / cypress",
        pixel_involvement="direct",
    ),
    WebEvidenceChannel.CROSS_LANGUAGE_STATIC: ChannelCapability(
        channel=WebEvidenceChannel.CROSS_LANGUAGE_STATIC,
        languages_checked=["python", "jinja2", "javascript", "html", "css", "sql"],
        trust_range=[
            WebTrustLevel.TEMPLATE_TYPE_CHECKED.value,
            WebTrustLevel.SERVER_VALIDATED.value,
        ],
        tooling="custom cross-language analyzer",
        pixel_involvement="indirect",
    ),
    WebEvidenceChannel.ACCESSIBILITY_AUDIT: ChannelCapability(
        channel=WebEvidenceChannel.ACCESSIBILITY_AUDIT,
        languages_checked=["html", "css"],
        trust_range=[
            WebTrustLevel.CLIENT_VALIDATED.value,
            WebTrustLevel.CLIENT_VALIDATED.value,
        ],
        tooling="axe-core / pa11y",
        pixel_involvement="direct",
    ),
    WebEvidenceChannel.VISUAL_REGRESSION: ChannelCapability(
        channel=WebEvidenceChannel.VISUAL_REGRESSION,
        languages_checked=["html", "css", "javascript"],
        trust_range=[
            WebTrustLevel.BROWSER_TESTED.value,
            WebTrustLevel.CSS_LINTED.value,
        ],
        tooling="percy / backstopjs",
        pixel_involvement="direct",
    ),
    WebEvidenceChannel.SECURITY_SCAN: ChannelCapability(
        channel=WebEvidenceChannel.SECURITY_SCAN,
        languages_checked=["python", "javascript", "html", "sql"],
        trust_range=[
            WebTrustLevel.SERVER_VALIDATED.value,
            WebTrustLevel.MIDDLEWARE_ENFORCED.value,
        ],
        tooling="bandit / semgrep / custom scanner",
        pixel_involvement="none",
    ),
}


@dataclass
class EvidenceGap:
    """Identifies a coordinate lacking sufficient evidence coverage."""

    coordinate_id: str
    missing_channels: list[str] = field(default_factory=list)
    min_trust_achieved: str = ""
    max_trust_possible: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "coordinate_id": self.coordinate_id,
            "missing_channels": list(self.missing_channels),
            "min_trust_achieved": self.min_trust_achieved,
            "max_trust_possible": self.max_trust_possible,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EvidenceGap:
        return cls(
            coordinate_id=data["coordinate_id"],
            missing_channels=data.get("missing_channels", []),
            min_trust_achieved=data.get("min_trust_achieved", ""),
            max_trust_possible=data.get("max_trust_possible", ""),
            recommendation=data.get("recommendation", ""),
        )
