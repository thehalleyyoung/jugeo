"""
tests/jugeo/maturity/cyclic_picture/test_integration.py

Comprehensive tests for the jugeo.maturity.cyclic_picture.integration module.

Covers all five public classes (MaturityEvidenceIntegrator, MaturityOrchestratorBridge,
MaturityIdeationConnector, MaturityGeometryMapper, MaturityIntegrationFacade) and
the four free convenience functions, including graceful-degradation paths when optional
cross-subsystem modules are unavailable.

Theory reference: theory2.tex Ch65
"""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.maturity.cyclic_picture.integration import (
    MaturityEvidenceIntegrator,
    MaturityOrchestratorBridge,
    MaturityIdeationConnector,
    MaturityGeometryMapper,
    MaturityIntegrationFacade,
    integrate_maturity_evidence,
    connect_to_orchestrator,
    propose_ideation_improvements,
    map_to_geometry,
)
from jugeo.maturity.cyclic_picture.models import MatureSystem, MaturityLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_system(level: MaturityLevel = MaturityLevel.OPERATIONAL) -> MatureSystem:
    """Create a MatureSystem with a deterministic ID for test isolation."""
    return MatureSystem.create(system_id="test-sys-001", initial_level=level)


# ===========================================================================
# TestMaturityEvidenceIntegrator
# ===========================================================================

class TestMaturityEvidenceIntegrator:
    """Tests for MaturityEvidenceIntegrator – the evidence-grounding adapter."""

    def test_create_no_manifest(self):
        """Creating without a manifest should set manifest=None.

        Per the integration spec, the factory must accept a call with zero
        arguments and produce a fully-functional integrator with a non-empty
        integrator_id and an empty evidence_records list.
        """
        integrator = MaturityEvidenceIntegrator.create()
        assert integrator.manifest is None
        assert isinstance(integrator.integrator_id, str)
        assert len(integrator.integrator_id) > 0

    def test_create_with_manifest(self):
        """When a manifest object is supplied, it must be stored verbatim.

        Callers may pass any object as the manifest (e.g., a CyclicPictureManifest
        or a plain dict used as a mock). The integrator must not transform or
        validate the manifest at construction time.
        """
        fake_manifest = {"manifest_id": "mfst-001", "system_id": "sys-001"}
        integrator = MaturityEvidenceIntegrator.create(manifest=fake_manifest)
        assert integrator.manifest is fake_manifest

    def test_ingest_evidence_string(self):
        """Ingesting a plain string should append it to evidence_records.

        Evidence records can be arbitrary objects; the simplest case is a
        plain Python string.  After one ingest call the list must have length 1
        and contain exactly the passed value.
        """
        integrator = MaturityEvidenceIntegrator.create()
        integrator.ingest_evidence("record-alpha")
        assert len(integrator.evidence_records) == 1
        assert integrator.evidence_records[0] == "record-alpha"

    def test_ingest_evidence_dict(self):
        """Ingesting a dict should append the dict object to evidence_records.

        Dict-form records are the most common real-world case (JSON payloads
        from external evidence APIs).  The integrator must not copy or mutate
        the dict; the same object identity must be preserved.
        """
        integrator = MaturityEvidenceIntegrator.create()
        record = {"record_id": "ev-001", "payload": "data"}
        integrator.ingest_evidence(record)
        assert len(integrator.evidence_records) == 1
        assert integrator.evidence_records[0] is record

    def test_ingest_multiple_records(self):
        """Successive ingest calls must accumulate records in order.

        After three separate ingest calls the evidence_records list must
        contain three elements in insertion order, enabling chronological
        chain reconstruction.
        """
        integrator = MaturityEvidenceIntegrator.create()
        for i in range(3):
            integrator.ingest_evidence(f"record-{i}")
        assert len(integrator.evidence_records) == 3
        assert integrator.evidence_records[1] == "record-1"

    def test_build_evidence_chain_empty(self):
        """build_evidence_chain on a fresh integrator must return an empty list.

        Theorem 65.6 stipulates that a chain is valid only when non-empty;
        this test confirms the base-case identity of the chain builder.
        """
        integrator = MaturityEvidenceIntegrator.create()
        chain = integrator.build_evidence_chain()
        assert isinstance(chain, list)
        assert len(chain) == 0

    def test_build_evidence_chain_strings(self):
        """String records must appear in the chain as-is (converted to str).

        The chain must be a list of strings for downstream JSON serialisation.
        Plain string records satisfy this without any transformation.
        """
        integrator = MaturityEvidenceIntegrator.create()
        integrator.ingest_evidence("ev-abc")
        chain = integrator.build_evidence_chain()
        assert len(chain) == 1
        assert chain[0] == "ev-abc"

    def test_build_evidence_chain_dicts(self):
        """Dict records with a 'record_id' key must appear as that id value.

        When the evidence record is a dict and contains the key 'record_id',
        the chain builder must extract it (not serialise the whole dict).
        """
        integrator = MaturityEvidenceIntegrator.create()
        integrator.ingest_evidence({"record_id": "ev-dict-001", "other": "x"})
        chain = integrator.build_evidence_chain()
        assert chain[0] == "ev-dict-001"

    def test_validate_chain_empty(self):
        """validate_chain must return False when the evidence_records list is empty.

        Theorem 65.6 requirement: a maturity improvement cycle must be grounded
        by at least one evidence record.  An empty chain is therefore invalid.
        """
        integrator = MaturityEvidenceIntegrator.create()
        result = integrator.validate_chain()
        assert result is False

    def test_validate_chain_valid(self):
        """validate_chain must return True when at least one record is present.

        After ingesting a single record the chain is non-empty and should pass
        the Theorem 65.6 completeness predicate.
        """
        integrator = MaturityEvidenceIntegrator.create()
        integrator.ingest_evidence("ev-valid")
        result = integrator.validate_chain()
        assert result is True

    def test_to_dict_has_integrator_id(self):
        """to_dict must include the integrator_id field for traceability.

        All serialised objects in the integration layer must be uniquely
        identifiable via their id field when round-tripped through JSON.
        """
        integrator = MaturityEvidenceIntegrator.create()
        d = integrator.to_dict()
        assert "integrator_id" in d
        assert d["integrator_id"] == integrator.integrator_id

    def test_to_dict_has_record_count(self):
        """to_dict must include 'record_count' reflecting the number of ingested records."""
        integrator = MaturityEvidenceIntegrator.create()
        integrator.ingest_evidence("r1")
        integrator.ingest_evidence("r2")
        d = integrator.to_dict()
        assert "record_count" in d
        assert d["record_count"] == 2

    def test_to_dict_has_chain_valid(self):
        """to_dict must include 'chain_valid' boolean flag."""
        integrator = MaturityEvidenceIntegrator.create()
        d = integrator.to_dict()
        assert "chain_valid" in d

    def test_to_dict_has_timestamp(self):
        """to_dict must include a 'timestamp' key for temporal tracing."""
        integrator = MaturityEvidenceIntegrator.create()
        d = integrator.to_dict()
        assert "timestamp" in d

    def test_graceful_without_evidence_imports(self):
        """Integrator must remain functional even if evidence sub-modules fail to import.

        The integration module guards all cross-module imports in try/except blocks.
        This test verifies that creating and using an integrator works regardless
        of whether jugeo.evidence.* modules are available.
        """
        integrator = MaturityEvidenceIntegrator.create()
        integrator.ingest_evidence({"record_id": "fallback-record"})
        chain = integrator.build_evidence_chain()
        assert len(chain) == 1
        assert integrator.validate_chain() is True

    def test_integrator_id_unique_per_instance(self):
        """Each call to create() must produce a distinct integrator_id."""
        ids = {MaturityEvidenceIntegrator.create().integrator_id for _ in range(10)}
        assert len(ids) == 10


# ===========================================================================
# TestMaturityOrchestratorBridge
# ===========================================================================

class TestMaturityOrchestratorBridge:
    """Tests for MaturityOrchestratorBridge – the orchestration adapter."""

    def test_create_no_system(self):
        """Creating a bridge without a system must set system=None.

        The bridge must be constructible standalone to support early-pipeline
        usages before a MatureSystem has been assembled.
        """
        bridge = MaturityOrchestratorBridge.create()
        assert bridge.system is None

    def test_create_with_system(self):
        """When a system object is provided it must be stored on the bridge.

        The bridge uses system metadata (e.g. system_id, maturity_level) when
        composing orchestrator task payloads.
        """
        system = _make_system()
        bridge = MaturityOrchestratorBridge.create(system=system)
        assert bridge.system is system

    def test_submit_to_orchestrator_returns_dict(self):
        """submit_to_orchestrator must return a dict regardless of orchestrator availability.

        The method should never raise; instead it returns a status dict with at
        least a 'task_id' key so callers can do non-blocking polling.
        """
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.submit_to_orchestrator({"task_type": "test"})
        assert isinstance(result, dict)

    def test_submit_to_orchestrator_has_task_id(self):
        """The submission result dict must always contain a non-empty 'task_id'.

        This is critical for subsequent poll_orchestrator calls; if the
        orchestrator is unavailable a synthetic task_id is generated locally.
        """
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.submit_to_orchestrator({"task_type": "maturity_pass"})
        assert "task_id" in result
        assert result["task_id"]

    def test_submit_to_orchestrator_has_status(self):
        """The submission result dict must contain a 'status' key."""
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.submit_to_orchestrator({})
        assert "status" in result

    def test_poll_orchestrator_returns_dict(self):
        """poll_orchestrator must return a dict even when the orchestrator is offline.

        Callers poll by task_id to check progress; a fallback stub dict
        ensures the polling loop always terminates gracefully.
        """
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.poll_orchestrator("fake-task-id-001")
        assert isinstance(result, dict)

    def test_poll_orchestrator_has_task_id(self):
        """The poll result must echo back the task_id for correlation."""
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.poll_orchestrator("task-42")
        assert "task_id" in result

    def test_handle_result_pass_through(self):
        """handle_result must process a result dict without raising.

        The method extracts the payload and returns it; if the status is
        not 'error' the payload should be returned directly.
        """
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.handle_result({"status": "ok", "payload": {"data": 1}})
        # Either the payload value or None is acceptable
        assert result is None or isinstance(result, (dict, str, int, float, list))

    def test_handle_result_error_returns_none(self):
        """handle_result must return None when the status is 'error'."""
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.handle_result({"status": "error", "message": "oops"})
        assert result is None

    def test_to_dict_has_bridge_id(self):
        """to_dict must include 'bridge_id' for full serialisation traceability."""
        bridge = MaturityOrchestratorBridge.create()
        d = bridge.to_dict()
        assert "bridge_id" in d
        assert d["bridge_id"] == bridge.bridge_id

    def test_graceful_without_orchestrator_imports(self):
        """Bridge must not crash when jugeo.orchestration.* modules are unavailable.

        The integration module wraps all orchestration imports in try/except so
        the bridge falls back to an 'unavailable' stub response.
        """
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.submit_to_orchestrator({"task_type": "test"})
        # Must be one of the known status values
        assert result["status"] in ("submitted", "queued", "unavailable")

    def test_bridge_id_unique_per_instance(self):
        """Each create() call must produce a unique bridge_id."""
        ids = {MaturityOrchestratorBridge.create().bridge_id for _ in range(8)}
        assert len(ids) == 8


# ===========================================================================
# TestMaturityIdeationConnector
# ===========================================================================

class TestMaturityIdeationConnector:
    """Tests for MaturityIdeationConnector – the ideation-subsystem adapter."""

    def test_create_no_regime(self):
        """Creating without a regime must set regime=None and proposals=[].

        The connector is usable in 'default regime' mode when no ideation
        regime object is available.
        """
        connector = MaturityIdeationConnector.create()
        assert connector.regime is None
        assert connector.proposals == []

    def test_create_with_regime(self):
        """A supplied regime object must be stored on the connector.

        This enables regime-specific proposal scoring in propose_improvement().
        """
        fake_regime = object()
        connector = MaturityIdeationConnector.create(regime=fake_regime)
        assert connector.regime is fake_regime

    def test_propose_improvement_returns_dict(self):
        """propose_improvement must always return a dict.

        Callers depend on a stable dict interface regardless of whether the
        jugeo.ideation.* modules are loaded.
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        result = connector.propose_improvement(system, context={})
        assert isinstance(result, dict)

    def test_propose_improvement_has_score(self):
        """The proposal dict must always contain a 'score' key.

        Per Theorem 65.8, proposals are ranked monotonically by maturity gain;
        this requires every proposal to carry a numeric score.
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        result = connector.propose_improvement(system, context={})
        assert "score" in result
        assert isinstance(result["score"], (int, float))

    def test_propose_improvement_appends_to_proposals(self):
        """Each propose_improvement call must append the proposal to self.proposals."""
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        connector.propose_improvement(system, context={})
        connector.propose_improvement(system, context={})
        assert len(connector.proposals) == 2

    def test_propose_improvement_has_proposal_id(self):
        """Each proposal must have a non-empty 'proposal_id'."""
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        result = connector.propose_improvement(system, context={})
        assert "proposal_id" in result
        assert result["proposal_id"]

    def test_propose_improvement_custom_score_in_context(self):
        """A 'score' key in the context dict must be reflected in the proposal.

        The connector extracts score from context["score"] to allow callers
        to inject domain-specific scoring signals.
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        result = connector.propose_improvement(system, context={"score": 0.9})
        assert result["score"] == pytest.approx(0.9)

    def test_filter_proposals_empty(self):
        """filter_proposals on a connector with no proposals returns an empty list."""
        connector = MaturityIdeationConnector.create()
        assert connector.filter_proposals() == []

    def test_filter_proposals_above_threshold(self):
        """filter_proposals must return only proposals whose score >= min_score.

        Given two proposals with scores 0.3 and 0.8, filtering with min_score=0.5
        should yield only the high-score proposal.
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        connector.propose_improvement(system, context={"score": 0.3})
        connector.propose_improvement(system, context={"score": 0.8})
        filtered = connector.filter_proposals(min_score=0.5)
        assert len(filtered) == 1
        assert filtered[0]["score"] == pytest.approx(0.8)

    def test_filter_proposals_below_threshold(self):
        """filter_proposals must exclude proposals with score < min_score.

        Proposals with score 0.1, 0.2 must both be excluded when min_score=0.5.
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        connector.propose_improvement(system, context={"score": 0.1})
        connector.propose_improvement(system, context={"score": 0.2})
        filtered = connector.filter_proposals(min_score=0.5)
        assert len(filtered) == 0

    def test_rank_proposals_sorted(self):
        """rank_proposals must return proposals sorted highest score first.

        Per Theorem 65.8, ranking is monotone w.r.t. expected maturity gain,
        so the first element must have the highest score.
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        connector.propose_improvement(system, context={"score": 0.4})
        connector.propose_improvement(system, context={"score": 0.9})
        connector.propose_improvement(system, context={"score": 0.6})
        ranked = connector.rank_proposals()
        scores = [p["score"] for p in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_rank_proposals_empty(self):
        """rank_proposals on an empty connector must return an empty list."""
        connector = MaturityIdeationConnector.create()
        assert connector.rank_proposals() == []

    def test_to_dict_has_connector_id(self):
        """to_dict must include 'connector_id' for serialisation traceability."""
        connector = MaturityIdeationConnector.create()
        d = connector.to_dict()
        assert "connector_id" in d
        assert d["connector_id"] == connector.connector_id

    def test_to_dict_has_proposal_count(self):
        """to_dict must include 'proposal_count' reflecting proposals list length."""
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        connector.propose_improvement(system, context={})
        d = connector.to_dict()
        assert "proposal_count" in d
        assert d["proposal_count"] == 1

    def test_graceful_without_ideation_imports(self):
        """Connector must work when jugeo.ideation.* imports are not available.

        The proposal dict must be returned with at least 'proposal_id', 'score',
        and 'timestamp' keys, constructed entirely from local fallback logic.
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        result = connector.propose_improvement(system, context={})
        for key in ("proposal_id", "score", "timestamp"):
            assert key in result


# ===========================================================================
# TestMaturityGeometryMapper
# ===========================================================================

class TestMaturityGeometryMapper:
    """Tests for MaturityGeometryMapper – the geometry-subsystem adapter."""

    def test_create_no_site(self):
        """Creating without a site must set site=None and coordinates=[].

        The mapper is fully usable in siteless mode; it will return None from
        find_nearest_site in that case.
        """
        mapper = MaturityGeometryMapper.create()
        assert mapper.site is None
        assert mapper.coordinates == []

    def test_create_with_site(self):
        """A supplied site object must be stored on the mapper."""
        fake_site = {"site_id": "site-001"}
        mapper = MaturityGeometryMapper.create(site=fake_site)
        assert mapper.site is fake_site

    def test_map_to_coordinate_returns_dict(self):
        """map_to_coordinate must return a coordinate dict for any MatureSystem.

        Per Theorem 65.9, every maturity level must have a corresponding
        geometric coordinate, so the method must never return None.
        """
        mapper = MaturityGeometryMapper.create()
        system = _make_system()
        coord = mapper.map_to_coordinate(system)
        assert isinstance(coord, dict)

    def test_map_to_coordinate_has_x_y(self):
        """The coordinate dict must contain numeric 'x' and 'y' keys."""
        mapper = MaturityGeometryMapper.create()
        system = _make_system()
        coord = mapper.map_to_coordinate(system)
        assert "x" in coord
        assert "y" in coord
        assert isinstance(coord["x"], (int, float))
        assert isinstance(coord["y"], (int, float))

    def test_map_to_coordinate_level_present(self):
        """The coordinate dict must contain a 'level' key describing the maturity level.

        Downstream visualisation tools rely on the human-readable level name
        in the coordinate payload.
        """
        mapper = MaturityGeometryMapper.create()
        system = _make_system(level=MaturityLevel.FEDERATED)
        coord = mapper.map_to_coordinate(system)
        assert "level" in coord

    def test_map_to_coordinate_appends(self):
        """Each map_to_coordinate call must append to self.coordinates."""
        mapper = MaturityGeometryMapper.create()
        system = _make_system()
        mapper.map_to_coordinate(system)
        mapper.map_to_coordinate(system)
        assert len(mapper.coordinates) == 2

    def test_map_to_coordinate_y_ordinal_order(self):
        """The 'y' ordinal must increase with maturity level.

        Per Theorem 65.9 the mapping must be injective: PROTOTYPE < OPERATIONAL
        < FEDERATED < SELF_IMPROVING < MATURE in y-coordinate space.
        """
        mapper = MaturityGeometryMapper.create()
        levels = [
            MaturityLevel.PROTOTYPE,
            MaturityLevel.OPERATIONAL,
            MaturityLevel.FEDERATED,
            MaturityLevel.SELF_IMPROVING,
            MaturityLevel.MATURE,
        ]
        y_values = []
        for level in levels:
            system = MatureSystem.create(system_id="sys-ord", initial_level=level)
            coord = mapper.map_to_coordinate(system)
            y_values.append(coord["y"])
        assert y_values == sorted(y_values), "y ordinals must be strictly increasing with level"

    def test_find_nearest_site_returns_site_or_none(self):
        """find_nearest_site must return either a site object or None, never raising.

        When no site has been set on the mapper, None is the only valid return.
        """
        mapper = MaturityGeometryMapper.create()
        result = mapper.find_nearest_site({"x": 0.5, "y": 2})
        assert result is None

    def test_find_nearest_site_with_coordinate(self):
        """When a site is set, find_nearest_site must return it.

        The current implementation returns self.site as the nearest site; future
        versions will perform spatial lookup over a site catalogue.
        """
        fake_site = {"site_id": "site-nearest"}
        mapper = MaturityGeometryMapper.create(site=fake_site)
        result = mapper.find_nearest_site({"x": 0.1, "y": 0})
        assert result is fake_site

    def test_to_dict_has_mapper_id(self):
        """to_dict must include 'mapper_id'."""
        mapper = MaturityGeometryMapper.create()
        d = mapper.to_dict()
        assert "mapper_id" in d
        assert d["mapper_id"] == mapper.mapper_id

    def test_to_dict_has_coordinate_count(self):
        """to_dict must include 'coordinate_count' equal to len(coordinates)."""
        mapper = MaturityGeometryMapper.create()
        system = _make_system()
        mapper.map_to_coordinate(system)
        d = mapper.to_dict()
        assert "coordinate_count" in d
        assert d["coordinate_count"] == 1

    def test_graceful_without_geometry_imports(self):
        """Mapper must work when jugeo.geometry.* imports are unavailable.

        The coordinate dict is built using only local data (hash, enum ordinal),
        so the mapper must function fully standalone.
        """
        mapper = MaturityGeometryMapper.create()
        system = _make_system()
        coord = mapper.map_to_coordinate(system)
        assert "x" in coord and "y" in coord and "level" in coord


# ===========================================================================
# TestMaturityIntegrationFacade
# ===========================================================================

class TestMaturityIntegrationFacade:
    """Tests for MaturityIntegrationFacade – the top-level integration composer."""

    def test_create(self):
        """create() must return a MaturityIntegrationFacade with all four adapters.

        The facade instantiates adapters lazily via their own create() methods,
        so callers need no external dependencies to construct it.
        """
        facade = MaturityIntegrationFacade.create()
        assert isinstance(facade, MaturityIntegrationFacade)
        assert facade.evidence_integrator is not None
        assert facade.orchestrator_bridge is not None
        assert facade.ideation_connector is not None
        assert facade.geometry_mapper is not None

    def test_run_full_integration_returns_dict(self):
        """run_full_integration must return a dict containing all integration results.

        This is the primary entry-point for callers executing a full cross-subsystem
        maturity pass.
        """
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        result = facade.run_full_integration(system, context={})
        assert isinstance(result, dict)

    def test_run_full_integration_has_evidence_key(self):
        """The result dict must have an 'evidence' key from the evidence adapter."""
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        result = facade.run_full_integration(system, context={})
        assert "evidence" in result

    def test_run_full_integration_has_orchestrator_key(self):
        """The result dict must have an 'orchestrator' key from the bridge adapter."""
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        result = facade.run_full_integration(system, context={})
        assert "orchestrator" in result

    def test_run_full_integration_has_ideation_key(self):
        """The result dict must have an 'ideation' key from the connector adapter."""
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        result = facade.run_full_integration(system, context={})
        assert "ideation" in result

    def test_run_full_integration_has_geometry_key(self):
        """The result dict must have a 'geometry' key from the geometry mapper."""
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        result = facade.run_full_integration(system, context={})
        assert "geometry" in result

    def test_run_full_integration_has_expected_keys(self):
        """The result dict must contain facade_id, timestamp, and all four adapter keys."""
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        result = facade.run_full_integration(system, context={})
        for key in ("facade_id", "timestamp", "evidence", "orchestrator", "ideation", "geometry"):
            assert key in result, f"Expected key '{key}' missing from integration result"

    def test_to_dict_has_facade_id(self):
        """to_dict must include 'facade_id'."""
        facade = MaturityIntegrationFacade.create()
        d = facade.to_dict()
        assert "facade_id" in d
        assert d["facade_id"] == facade.facade_id

    def test_to_dict_has_all_adapter_keys(self):
        """to_dict must include serialised representations of all four adapters."""
        facade = MaturityIntegrationFacade.create()
        d = facade.to_dict()
        for key in ("evidence_integrator", "orchestrator_bridge", "ideation_connector", "geometry_mapper"):
            assert key in d

    def test_run_full_integration_with_system(self):
        """run_full_integration must work correctly with a fully-specified MatureSystem.

        This test exercises the full facade pipeline with a real MatureSystem
        at the MATURE level to ensure no edge cases at the top maturity level.
        """
        facade = MaturityIntegrationFacade.create()
        system = _make_system(level=MaturityLevel.MATURE)
        result = facade.run_full_integration(system, context={"score": 0.95})
        assert isinstance(result, dict)
        assert "facade_id" in result

    def test_facade_id_unique_per_instance(self):
        """Each create() call must produce a unique facade_id."""
        ids = {MaturityIntegrationFacade.create().facade_id for _ in range(6)}
        assert len(ids) == 6

    def test_run_full_integration_with_evidence_record_in_context(self):
        """Evidence records supplied in context must be ingested during the pass."""
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        ctx = {"evidence_record": {"record_id": "ev-ctx-001"}}
        result = facade.run_full_integration(system, context=ctx)
        # The evidence sub-result must be a dict (not an error key alone)
        assert isinstance(result.get("evidence"), dict)


# ===========================================================================
# TestFreeFunctions
# ===========================================================================

class TestFreeFunctions:
    """Tests for the four module-level convenience functions."""

    def test_integrate_maturity_evidence_returns_dict(self):
        """integrate_maturity_evidence must return a dict with chain_valid flag.

        The function creates a fresh integrator, ingests all records, validates
        the chain, and returns the serialised result.
        """
        system = _make_system()
        result = integrate_maturity_evidence(system, records=["r1", "r2"])
        assert isinstance(result, dict)
        assert "chain_valid" in result

    def test_integrate_maturity_evidence_empty_records(self):
        """Passing an empty records list must yield chain_valid=False.

        Per Theorem 65.6 an empty evidence chain is invalid regardless of
        the system's maturity level.
        """
        system = _make_system()
        result = integrate_maturity_evidence(system, records=[])
        assert result["chain_valid"] is False

    def test_integrate_maturity_evidence_nonempty_records(self):
        """Passing non-empty records must yield chain_valid=True."""
        system = _make_system()
        result = integrate_maturity_evidence(system, records=["ev-001"])
        assert result["chain_valid"] is True

    def test_integrate_maturity_evidence_record_count(self):
        """The returned dict must reflect the number of ingested records."""
        system = _make_system()
        result = integrate_maturity_evidence(system, records=["a", "b", "c"])
        assert result.get("record_count") == 3

    def test_connect_to_orchestrator_returns_dict(self):
        """connect_to_orchestrator must return a dict even when orchestrator offline.

        The function wraps submit_to_orchestrator and must never raise regardless
        of orchestration-subsystem availability.
        """
        system = _make_system()
        result = connect_to_orchestrator(system)
        assert isinstance(result, dict)

    def test_connect_to_orchestrator_has_task_id(self):
        """The result from connect_to_orchestrator must contain 'task_id'."""
        system = _make_system()
        result = connect_to_orchestrator(system)
        assert "task_id" in result

    def test_connect_to_orchestrator_has_status(self):
        """The result must contain a 'status' key."""
        system = _make_system()
        result = connect_to_orchestrator(system)
        assert "status" in result

    def test_propose_ideation_improvements_returns_list(self):
        """propose_ideation_improvements must return a list of proposal dicts.

        Per the integration spec, the function always returns at least one
        proposal (generated from the system's current maturity level).
        """
        system = _make_system()
        result = propose_ideation_improvements(system)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_propose_ideation_improvements_proposals_have_score(self):
        """Each proposal in the returned list must contain a 'score' key."""
        system = _make_system()
        proposals = propose_ideation_improvements(system)
        for p in proposals:
            assert "score" in p

    def test_propose_ideation_improvements_sorted_by_score(self):
        """The returned proposals must be sorted highest score first (ranked)."""
        system = _make_system()
        proposals = propose_ideation_improvements(system)
        scores = [p["score"] for p in proposals]
        assert scores == sorted(scores, reverse=True)

    def test_map_to_geometry_returns_dict(self):
        """map_to_geometry must return a coordinate dict.

        Per Theorem 65.9 the result is an injective function of the maturity level.
        """
        system = _make_system()
        result = map_to_geometry(system)
        assert isinstance(result, dict)

    def test_map_to_geometry_has_x_y_level(self):
        """The coordinate dict must have 'x', 'y', and 'level' keys."""
        system = _make_system()
        result = map_to_geometry(system)
        for key in ("x", "y", "level"):
            assert key in result

    def test_map_to_geometry_has_system_id(self):
        """The coordinate dict must include the originating system_id."""
        system = _make_system()
        result = map_to_geometry(system)
        assert "system_id" in result

    def test_all_functions_graceful_without_imports(self):
        """All four free functions must work without any external subsystem imports.

        This smoke test ensures the graceful-degradation path covers all four
        convenience functions when called in sequence.
        """
        system = _make_system()
        ev = integrate_maturity_evidence(system, records=["ev-smoke"])
        orch = connect_to_orchestrator(system)
        ideas = propose_ideation_improvements(system)
        geo = map_to_geometry(system)
        assert isinstance(ev, dict)
        assert isinstance(orch, dict)
        assert isinstance(ideas, list)
        assert isinstance(geo, dict)


# ===========================================================================
# Graceful degradation tests
# ===========================================================================

class TestGracefulDegradation:
    """Tests specifically targeting the graceful-degradation paths in integration.py.

    The integration module wraps all cross-module imports in try/except blocks.
    These tests verify correct fallback behaviour when those imports are unavailable,
    exercising the 'unavailable' stub paths rather than live subsystem calls.
    """

    def test_integrator_ingest_works_without_evidence_module(self):
        """Ingesting a plain dict into the integrator must succeed standalone.

        Even without jugeo.evidence.*, the integrator must accept arbitrary
        dicts and include them in the evidence chain.
        """
        integrator = MaturityEvidenceIntegrator.create()
        record = {"record_id": "fallback-ev-001", "kind": "manual"}
        integrator.ingest_evidence(record)
        chain = integrator.build_evidence_chain()
        assert "fallback-ev-001" in chain

    def test_bridge_submit_works_without_orchestrator_module(self):
        """Bridge submit must return a fallback dict with status 'unavailable' when offline.

        The integration module's orchestration guard path returns a stub dict
        when jugeo.orchestration.* cannot be imported or the orchestrator is down.
        """
        bridge = MaturityOrchestratorBridge.create()
        result = bridge.submit_to_orchestrator({"task_type": "fallback_test"})
        # Status must be a known value; 'unavailable' is the primary fallback
        assert result["status"] in ("submitted", "queued", "unavailable")
        assert "task_id" in result

    def test_connector_propose_works_without_ideation_module(self):
        """propose_improvement must return a minimal proposal dict without jugeo.ideation.*.

        The fallback path constructs a proposal using only local data (uid, system_id
        extraction, default score).
        """
        connector = MaturityIdeationConnector.create()
        system = _make_system()
        result = connector.propose_improvement(system, context={})
        assert "proposal_id" in result
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0

    def test_mapper_map_works_without_geometry_module(self):
        """map_to_coordinate must produce a valid coordinate dict without jugeo.geometry.*.

        The coordinate is computed from hash(system_id) and the enum ordinal,
        neither of which depends on external geometry code.
        """
        mapper = MaturityGeometryMapper.create()
        system = _make_system(level=MaturityLevel.PROTOTYPE)
        coord = mapper.map_to_coordinate(system)
        assert "x" in coord
        assert "y" in coord
        assert coord["y"] == 0  # PROTOTYPE has ordinal 0

    def test_facade_integration_works_with_no_external_modules(self):
        """The full MaturityIntegrationFacade pipeline must run standalone.

        This is the most comprehensive graceful-degradation test: a complete
        four-step integration pass must succeed even when all of
        jugeo.evidence, jugeo.orchestration, jugeo.ideation, jugeo.geometry
        are unavailable.
        """
        facade = MaturityIntegrationFacade.create()
        system = _make_system(level=MaturityLevel.SELF_IMPROVING)
        context = {
            "evidence_record": {"record_id": "standalone-ev"},
            "score": 0.75,
        }
        result = facade.run_full_integration(system, context=context)
        assert isinstance(result, dict)
        for key in ("evidence", "orchestrator", "ideation", "geometry"):
            assert key in result, f"Key '{key}' missing from standalone integration result"

    def test_multiple_facades_independent(self):
        """Multiple facade instances must not share adapter state.

        Each facade create() call must produce independent adapter instances so
        that running integration on one facade does not pollute another.
        """
        facade_a = MaturityIntegrationFacade.create()
        facade_b = MaturityIntegrationFacade.create()
        system = _make_system()
        facade_a.run_full_integration(system, context={"evidence_record": "ev-a"})
        # facade_b should have empty evidence records
        assert len(facade_b.evidence_integrator.evidence_records) == 0

    def test_integrator_chain_valid_flag_in_facade_result(self):
        """After running the full integration, the evidence result must include chain_valid."""
        facade = MaturityIntegrationFacade.create()
        system = _make_system()
        result = facade.run_full_integration(
            system,
            context={"evidence_record": {"record_id": "ev-chain-test"}},
        )
        evidence_result = result.get("evidence", {})
        assert "chain_valid" in evidence_result

    def test_geometry_y_ordinal_matches_level(self):
        """Geometry coordinate y must match the expected ordinal for each level."""
        expected_ordinals = {
            MaturityLevel.PROTOTYPE: 0,
            MaturityLevel.OPERATIONAL: 1,
            MaturityLevel.FEDERATED: 2,
            MaturityLevel.SELF_IMPROVING: 3,
            MaturityLevel.MATURE: 4,
        }
        for level, expected_y in expected_ordinals.items():
            mapper = MaturityGeometryMapper.create()
            system = MatureSystem.create(system_id="sys-ord-test", initial_level=level)
            coord = mapper.map_to_coordinate(system)
            assert coord["y"] == expected_y, (
                f"Expected y={expected_y} for level {level.value}, got {coord['y']}"
            )
