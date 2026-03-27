from __future__ import annotations

"""Public API for the gofai_chat knowledge package.

Exports the main public classes from each submodule.  Every import is
wrapped in a try/except so that a missing or broken submodule degrades
gracefully rather than bringing down the entire package.
"""

__all__: list[str] = []

# ── frame_kb ──────────────────────────────────────────────────────────────────
try:
    from gofai_chat.knowledge.frame_kb import FrameKB, FrameDefinition, FrameRole

    __all__ += ["FrameKB", "FrameDefinition", "FrameRole"]
except Exception:  # pragma: no cover
    pass

# ── event_kb ──────────────────────────────────────────────────────────────────
try:
    from gofai_chat.knowledge.event_kb import EventKB, EventSchema, EventChainReasoner

    __all__ += ["EventKB", "EventSchema", "EventChainReasoner"]
except Exception:  # pragma: no cover
    pass

# ── commonsense_kb ────────────────────────────────────────────────────────────
try:
    from gofai_chat.knowledge.commonsense_kb import (
        CommonsenseKB,
        CommonsenseFact,
        CommonsenseRelation,
        CommonsenseReasoner,
    )

    __all__ += [
        "CommonsenseKB",
        "CommonsenseFact",
        "CommonsenseRelation",
        "CommonsenseReasoner",
    ]
except Exception:  # pragma: no cover
    pass

# ── lexical_kb ────────────────────────────────────────────────────────────────
try:
    from gofai_chat.knowledge.lexical_kb import LexicalKB, LexicalEntry, LexicalRelation

    __all__ += ["LexicalKB", "LexicalEntry", "LexicalRelation"]
except Exception:  # pragma: no cover
    pass

# ── world_model ───────────────────────────────────────────────────────────────
try:
    from gofai_chat.knowledge.world_model import WorldModel, WorldState, WorldUpdater

    __all__ += ["WorldModel", "WorldState", "WorldUpdater"]
except Exception:  # pragma: no cover
    pass

# ── ontology ──────────────────────────────────────────────────────────────────
try:
    from gofai_chat.knowledge.ontology import (
        OntologyNode,
        Ontology,
        OntologyQuery,
        OntologyReasoner,
        OntologySerializer,
        OntologyIndex,
        ConceptualDistance,
        build_core_ontology,
        CORE_ONTOLOGY,
    )

    __all__ += [
        "OntologyNode",
        "Ontology",
        "OntologyQuery",
        "OntologyReasoner",
        "OntologySerializer",
        "OntologyIndex",
        "ConceptualDistance",
        "build_core_ontology",
        "CORE_ONTOLOGY",
    ]
except Exception:  # pragma: no cover
    pass

# ── event_structure ───────────────────────────────────────────────────────────
try:
    from gofai_chat.knowledge.event_structure import (
        AspectClass,
        EventStructure,
        TemporalRelation,
        EventChain,
        EventStructureAnalyzer,
        TemporalReasoningEngine,
        AspectShiftDetector,
        EventSchemaLibrary,
    )

    __all__ += [
        "AspectClass",
        "EventStructure",
        "TemporalRelation",
        "EventChain",
        "EventStructureAnalyzer",
        "TemporalReasoningEngine",
        "AspectShiftDetector",
        "EventSchemaLibrary",
    ]
except Exception:  # pragma: no cover
    pass
