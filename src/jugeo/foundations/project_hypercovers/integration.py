"""Theory2.tex Ch8 §8.1–§8.4 — Integration of the project_hypercovers subsystem
with other jugeo subsystems: descent, judgments, evidence, and oracle federation.

copilot: shared-core integration module — bridges project_hypercovers to the rest
of the JuGeo trust stack for LLM-assisted verification workflows.
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.hypercovers import HypercoverLevel, CechNerve, HypercoverKind
from jugeo.geometry.descent import DescentEngine, DescentResult, LocalSection, GluingData
from jugeo.geometry.site import CoordinateObject, SemanticSite, CoordinateKind
from jugeo.geometry.covers import Cover, CoverMetric
from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind
from jugeo.evidence.certificates import Certificate, CertificateStatus
from jugeo.foundations.project_hypercovers.models import (
    ProjectSite, ModuleCover, FleetMember, HypercoverDecomposition,
    ProjectKind, CoverStrategy, FleetStatus, DecompositionStatus,
    CoordinateMorphism, OverlapCell, CohomologyClass, TrustTier,
)

try:
    from jugeo.foundations.project_hypercovers.algorithms import (
        greedy_cover_algorithm,
        optimal_fleet_assignment,
        hypercover_descent_algorithm,
        cech_complex_computation,
        trust_propagation_algorithm,
    )
    _ALGORITHMS_AVAILABLE = True
except ImportError:
    _ALGORITHMS_AVAILABLE = False

# Module-level site registry
_SITE_REGISTRY: dict[str, ProjectSite] = {}


@dataclass(slots=True)
class ProjectHypercoverIntegration:
    """Main integration class connecting the project_hypercovers subsystem
    to the rest of the JuGeo trust stack.

    Theory2.tex §8.1–§8.4.

    Attributes
    ----------
    site : ProjectSite or None
        The project site under verification.
    cover : ModuleCover or None
        The current module cover.
    decomp : HypercoverDecomposition or None
        The hypercover decomposition.
    fleet : list[FleetMember]
        Assigned fleet members.
    _descent_engine : DescentEngine
        Internal descent engine.
    _certificates : list[Certificate]
        Accumulated certificates.
    config : dict[str, Any]
        Integration configuration.
    """

    site: ProjectSite | None = None
    cover: ModuleCover | None = None
    decomp: HypercoverDecomposition | None = None
    fleet: list[FleetMember] = field(default_factory=list)
    _descent_engine: DescentEngine = field(default_factory=DescentEngine)
    _certificates: list[Certificate] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def integrate_with_descent_engine(self, engine: DescentEngine) -> None:
        """Set the descent engine and configure it from available decomp/cover data.

        Wires the supplied ``DescentEngine`` into this integration object.  Any
        metadata available from the currently attached decomposition or module
        cover is pushed into the engine so that descent computations are
        context-aware.  The event is logged into ``self.config`` with a
        wall-clock timestamp so that audit trails remain intact.

        Parameters
        ----------
        engine : DescentEngine
            The descent engine to integrate.

        Returns
        -------
        None
        """
        self._descent_engine = engine
        engine_config: dict[str, Any] = {}

        if self.decomp is not None:
            engine_config['decomp_id'] = getattr(self.decomp, 'decomp_id', '')
            engine_config['site_id'] = getattr(self.decomp, 'site_id', '')
            raw_levels = getattr(self.decomp, 'levels', {}) or {}
            engine_config['level_count'] = len(raw_levels)
            engine_config['status'] = str(getattr(self.decomp, 'status', ''))
            engine_config['decomp_kind'] = str(getattr(self.decomp, 'kind', ''))
            cohomology = getattr(self.decomp, 'cohomology_classes', []) or []
            engine_config['cohomology_class_count'] = len(cohomology)
            for attr, val in engine_config.items():
                try:
                    if hasattr(engine, attr):
                        object.__setattr__(engine, attr, val)
                except (AttributeError, TypeError):
                    pass

        if self.cover is not None:
            engine_config['cover_id'] = getattr(self.cover, 'cover_id', '')
            engine_config['patch_count'] = getattr(self.cover, 'patch_count', 0)
            engine_config['cover_strategy'] = str(getattr(self.cover, 'strategy', ''))
            is_admissible = getattr(self.cover, 'is_admissible', False)
            engine_config['cover_admissible'] = bool(is_admissible)
            try:
                if hasattr(engine, 'cover_id'):
                    object.__setattr__(engine, 'cover_id', engine_config['cover_id'])
            except (AttributeError, TypeError):
                pass

        self.config['descent_engine_integration'] = {
            'timestamp': time.time(),
            'engine_type': type(engine).__name__,
            **engine_config,
        }

    def integrate_with_judgment_system(
        self, judgments: list[JudgmentTerm]
    ) -> dict[str, Any]:
        """Map each JudgmentTerm to a coordinate in the current site.

        Iterates over the supplied list of ``JudgmentTerm`` objects and attempts
        to match each one to a coordinate object in ``self.site``.  For matched
        coordinates a ``LocalSection`` is constructed so downstream descent
        machinery can operate directly on the judgment evidence.

        Parameters
        ----------
        judgments : list[JudgmentTerm]
            Judgment terms to integrate with the project site.

        Returns
        -------
        dict[str, Any]
            A mapping with keys ``'matched'``, ``'unmatched'``,
            ``'local_sections'``, and ``'judgment_count'``.
        """
        matched: list[dict[str, Any]] = []
        unmatched: list[str] = []
        local_sections: list[LocalSection] = []

        site_coords: list[CoordinateObject] = []
        if self.site is not None:
            raw_coords = getattr(self.site, 'coordinates', []) or []
            site_coords = list(raw_coords)

        coord_index: dict[str, CoordinateObject] = {}
        for coord in site_coords:
            cid = getattr(coord, 'coord_id', None) or getattr(coord, 'name', None) or str(id(coord))
            coord_index[cid] = coord

        for judgment in judgments:
            term_id: str
            if hasattr(judgment, 'term_id') and judgment.term_id:
                term_id = str(judgment.term_id)
            elif hasattr(judgment, 'formula') and judgment.formula:
                term_id = str(judgment.formula)
            else:
                term_id = str(judgment)

            matched_coord: CoordinateObject | None = None
            for cid, coord in coord_index.items():
                coord_name = getattr(coord, 'name', '') or ''
                if term_id == cid or term_id in coord_name or coord_name in term_id:
                    matched_coord = coord
                    break

            if matched_coord is not None:
                kind_val = getattr(judgment, 'kind', JudgmentKind.UNKNOWN) if hasattr(judgment, 'kind') else None
                section_data: dict[str, Any] = {
                    'term_id': term_id,
                    'coord_id': getattr(matched_coord, 'coord_id', ''),
                    'kind': str(kind_val),
                }
                try:
                    ls = LocalSection(
                        section_id=str(uuid.uuid4()),
                        coord=matched_coord,
                        data=section_data,
                    )
                    local_sections.append(ls)
                except Exception:
                    ls_dict = section_data
                matched: list[dict[str, Any]]
                matched.append({
                    'term_id': term_id,
                    'coord_id': getattr(matched_coord, 'coord_id', ''),
                    'section_built': True,
                })
            else:
                unmatched.append(term_id)

        self.config['judgment_integration'] = {
            'timestamp': time.time(),
            'judgment_count': len(judgments),
            'matched_count': len(matched),
            'unmatched_count': len(unmatched),
        }

        return {
            'matched': matched,
            'unmatched': unmatched,
            'local_sections': local_sections,
            'judgment_count': len(judgments),
        }

    def integrate_with_evidence_system(
        self, certificates: list[Certificate]
    ) -> dict[str, CertificateStatus]:
        """Integrate evidence certificates with the current site and fleet.

        For each ``Certificate`` supplied, the method checks whether its
        associated coordinate matches any coordinate in ``self.site``.  Fleet
        members whose assigned patches overlap with the certificate's coordinate
        receive a trust boost proportional to the certificate's trust level.
        Every certificate is recorded in ``self._certificates``.

        Parameters
        ----------
        certificates : list[Certificate]
            Evidence certificates to integrate.

        Returns
        -------
        dict[str, CertificateStatus]
            Mapping from coordinate ID to the resulting ``CertificateStatus``.
        """
        result: dict[str, CertificateStatus] = {}

        site_coord_ids: set[str] = set()
        if self.site is not None:
            for coord in (getattr(self.site, 'coordinates', []) or []):
                cid = getattr(coord, 'coord_id', None) or getattr(coord, 'name', None)
                if cid:
                    site_coord_ids.add(str(cid))

        for cert in certificates:
            cert_coord_id = str(getattr(cert, 'coord_id', '') or getattr(cert, 'coordinate_id', '') or '')
            cert_status: CertificateStatus = getattr(cert, 'status', CertificateStatus.PENDING)
            cert_trust = float(getattr(cert, 'trust_level', 0.0) or 0.0)

            if cert_coord_id in site_coord_ids:
                for member in self.fleet:
                    member_patches = getattr(member, 'patch_ids', []) or []
                    if cert_coord_id in [str(p) for p in member_patches]:
                        current_trust = float(getattr(member, 'trust_level', 0.0) or 0.0)
                        boosted = min(1.0, current_trust + cert_trust * 0.1)
                        try:
                            object.__setattr__(member, 'trust_level', boosted)
                        except (AttributeError, TypeError):
                            pass

                if cert_status == CertificateStatus.PENDING:
                    try:
                        object.__setattr__(cert, 'status', CertificateStatus.VERIFIED)
                        cert_status = CertificateStatus.VERIFIED
                    except (AttributeError, TypeError):
                        cert_status = CertificateStatus.VERIFIED

                result[cert_coord_id] = cert_status
            else:
                result[cert_coord_id] = CertificateStatus.UNVERIFIABLE

            self._certificates.append(cert)

        self.config['evidence_integration'] = {
            'timestamp': time.time(),
            'certificate_count': len(certificates),
            'verified_count': sum(1 for s in result.values() if s == CertificateStatus.VERIFIED),
            'unverifiable_count': sum(1 for s in result.values() if s == CertificateStatus.UNVERIFIABLE),
        }

        return result

    def integrate_with_oracle_federation(
        self, oracle_configs: list[dict[str, Any]]
    ) -> list[FleetMember]:
        """Create and register fleet members from oracle configuration entries.

        Each entry in ``oracle_configs`` describes a remote oracle: its
        identifier, capability set, trust level, and the patch IDs it is
        responsible for.  A ``FleetMember`` is instantiated for every entry and
        appended to ``self.fleet``.

        Parameters
        ----------
        oracle_configs : list[dict[str, Any]]
            Oracle configuration dicts, each with keys ``oracle_id``,
            ``capabilities``, ``trust_level``, and ``patch_ids``.

        Returns
        -------
        list[FleetMember]
            Newly created fleet members (does not include pre-existing ones).
        """
        new_members: list[FleetMember] = []

        for cfg in oracle_configs:
            oracle_id = str(cfg.get('oracle_id', str(uuid.uuid4())))
            capabilities = list(cfg.get('capabilities', []))
            trust_level = float(cfg.get('trust_level', 0.5))
            patch_ids = list(cfg.get('patch_ids', []))
            agent_kind = str(cfg.get('agent_kind', 'oracle'))
            description = str(cfg.get('description', f'Oracle {oracle_id}'))

            trust_tier: TrustTier
            if trust_level >= 0.9:
                trust_tier = TrustTier.HIGH
            elif trust_level >= 0.6:
                trust_tier = TrustTier.MEDIUM
            else:
                trust_tier = TrustTier.LOW

            try:
                member = FleetMember(
                    member_id=oracle_id,
                    agent_kind=agent_kind,
                    capabilities=capabilities,
                    trust_level=trust_level,
                    trust_tier=trust_tier,
                    patch_ids=patch_ids,
                    status=FleetStatus.ACTIVE,
                    description=description,
                    metadata={'source': 'oracle_federation', 'config': cfg},
                )
            except TypeError:
                member = FleetMember(
                    member_id=oracle_id,
                    agent_kind=agent_kind,
                    capabilities=capabilities,
                    trust_level=trust_level,
                    patch_ids=patch_ids,
                )

            new_members.append(member)
            self.fleet.append(member)

        self.config['oracle_federation_integration'] = {
            'timestamp': time.time(),
            'oracle_count': len(oracle_configs),
            'new_member_ids': [getattr(m, 'member_id', '') for m in new_members],
        }

        return new_members

    def build_fleet_from_config(
        self, config: list[dict[str, Any]]
    ) -> list[FleetMember]:
        """Construct a complete fleet from a structured configuration list.

        Each dict in ``config`` may contain the keys ``member_id``,
        ``agent_kind``, ``capabilities``, ``trust_level``, and
        ``max_patches``.  The resulting ``FleetMember`` objects replace
        ``self.fleet`` entirely.

        Parameters
        ----------
        config : list[dict[str, Any]]
            Fleet member configuration entries.

        Returns
        -------
        list[FleetMember]
            The newly constructed fleet, also stored as ``self.fleet``.
        """
        members: list[FleetMember] = []

        for entry in config:
            member_id = str(entry.get('member_id', str(uuid.uuid4())))
            agent_kind = str(entry.get('agent_kind', 'generic'))
            capabilities = list(entry.get('capabilities', []))
            trust_level = float(entry.get('trust_level', 0.5))
            max_patches = int(entry.get('max_patches', 10))
            patch_ids = list(entry.get('patch_ids', []))
            description = str(entry.get('description', f'Fleet member {member_id}'))

            trust_tier: TrustTier
            if trust_level >= 0.85:
                trust_tier = TrustTier.HIGH
            elif trust_level >= 0.55:
                trust_tier = TrustTier.MEDIUM
            else:
                trust_tier = TrustTier.LOW

            status_str = str(entry.get('status', 'active')).upper()
            try:
                status = FleetStatus[status_str]
            except KeyError:
                status = FleetStatus.ACTIVE

            try:
                member = FleetMember(
                    member_id=member_id,
                    agent_kind=agent_kind,
                    capabilities=capabilities,
                    trust_level=trust_level,
                    trust_tier=trust_tier,
                    patch_ids=patch_ids,
                    status=status,
                    max_patches=max_patches,
                    description=description,
                    metadata=entry.get('metadata', {}),
                )
            except TypeError:
                member = FleetMember(
                    member_id=member_id,
                    agent_kind=agent_kind,
                    capabilities=capabilities,
                    trust_level=trust_level,
                    patch_ids=patch_ids,
                )

            members.append(member)

        self.fleet = members
        self.config['fleet_build'] = {
            'timestamp': time.time(),
            'member_count': len(members),
            'member_ids': [getattr(m, 'member_id', '') for m in members],
        }

        return members

    def export_to_certificate(
        self, coord_id: str, propositions: list[str]
    ) -> Certificate:
        """Aggregate fleet trust and produce a Certificate for a coordinate.

        Scans all fleet members to find those whose ``patch_ids`` include
        ``coord_id``, computes the average trust level across those members,
        and constructs a ``Certificate`` with the aggregated trust level and the
        supplied ``propositions`` as verified propositions.

        Parameters
        ----------
        coord_id : str
            The coordinate identifier to certify.
        propositions : list[str]
            Proposition strings that the certificate attests to.

        Returns
        -------
        Certificate
            A populated certificate for the given coordinate.
        """
        relevant_members = [
            m for m in self.fleet
            if coord_id in [str(p) for p in (getattr(m, 'patch_ids', []) or [])]
        ]

        if relevant_members:
            trust_sum = sum(float(getattr(m, 'trust_level', 0.0) or 0.0) for m in relevant_members)
            avg_trust = trust_sum / len(relevant_members)
        else:
            avg_trust = 0.0

        cert_id = hashlib.sha256(
            f"{coord_id}:{':'.join(sorted(propositions))}:{time.time()}".encode()
        ).hexdigest()[:32]

        existing_certs_for_coord = [
            c for c in self._certificates
            if str(getattr(c, 'coord_id', '') or getattr(c, 'coordinate_id', '')) == coord_id
        ]
        if existing_certs_for_coord:
            prev_trust = float(
                getattr(existing_certs_for_coord[-1], 'trust_level', 0.0) or 0.0
            )
            avg_trust = (avg_trust + prev_trust) / 2.0

        cert_status = CertificateStatus.VERIFIED if avg_trust >= 0.5 else CertificateStatus.PENDING

        try:
            cert = Certificate(
                certificate_id=cert_id,
                coord_id=coord_id,
                trust_level=avg_trust,
                status=cert_status,
                verified_propositions=propositions,
                issuer=type(self).__name__,
                issued_at=time.time(),
                metadata={
                    'source': 'export_to_certificate',
                    'relevant_member_count': len(relevant_members),
                    'coord_id': coord_id,
                },
            )
        except TypeError:
            cert = Certificate(
                certificate_id=cert_id,
                coord_id=coord_id,
                trust_level=avg_trust,
                status=cert_status,
                verified_propositions=propositions,
            )

        self._certificates.append(cert)
        return cert

    def run_full_pipeline(
        self, sections: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute the complete hypercover-descent-certification pipeline.

        If both ``self.site`` and ``self.decomp`` are set and the algorithms
        module is available, delegates to ``hypercover_descent_algorithm``.
        Otherwise falls back to the internal ``_descent_engine``.  Certificates
        are exported for every coordinate in the site and a trust summary is
        computed.

        Parameters
        ----------
        sections : dict[str, Any]
            Input sections keyed by coordinate ID, passed to the descent
            engine or algorithm.

        Returns
        -------
        dict[str, Any]
            Result dict with keys ``descent_result``, ``certificates``,
            and ``trust_summary``.
        """
        descent_result: Any = None
        certificates: list[Certificate] = []
        trust_summary: dict[str, float] = {}

        if self.site is not None and self.decomp is not None and _ALGORITHMS_AVAILABLE:
            try:
                descent_result = hypercover_descent_algorithm(
                    site=self.site,
                    decomp=self.decomp,
                    sections=sections,
                )
            except Exception as exc:
                descent_result = {'error': str(exc), 'fallback': True}

        if descent_result is None or (isinstance(descent_result, dict) and descent_result.get('fallback')):
            try:
                gluing = GluingData(sections=sections, site_id=getattr(self.site, 'site_id', '') if self.site else '')
                descent_result = self._descent_engine.run(gluing_data=gluing)
            except Exception as exc:
                descent_result = {'error': str(exc), 'engine_fallback': True, 'sections': sections}

        site_coords: list[CoordinateObject] = []
        if self.site is not None:
            site_coords = list(getattr(self.site, 'coordinates', []) or [])

        for coord in site_coords:
            cid = str(getattr(coord, 'coord_id', None) or getattr(coord, 'name', None) or id(coord))
            props = list(sections.get(cid, {}).keys()) if isinstance(sections.get(cid), dict) else []
            if not props:
                props = [f'prop:{cid}']
            cert = self.export_to_certificate(cid, props)
            certificates.append(cert)
            trust_summary[cid] = float(getattr(cert, 'trust_level', 0.0) or 0.0)

        overall_trust = (
            sum(trust_summary.values()) / len(trust_summary)
            if trust_summary else 0.0
        )

        self.config['pipeline_run'] = {
            'timestamp': time.time(),
            'coord_count': len(site_coords),
            'certificate_count': len(certificates),
            'overall_trust': overall_trust,
        }

        return {
            'descent_result': descent_result,
            'certificates': certificates,
            'trust_summary': trust_summary,
            'overall_trust': overall_trust,
        }

    def set_site(self, site: ProjectSite) -> None:
        """Set the project site for this integration.

        Parameters
        ----------
        site : ProjectSite
            The project site to attach.
        """
        self.site = site
        self.config['site_set'] = {
            'timestamp': time.time(),
            'site_id': getattr(site, 'site_id', ''),
        }

    def set_cover(self, cover: ModuleCover) -> None:
        """Set the module cover for this integration.

        Parameters
        ----------
        cover : ModuleCover
            The module cover to attach.
        """
        self.cover = cover
        self.config['cover_set'] = {
            'timestamp': time.time(),
            'cover_id': getattr(cover, 'cover_id', ''),
        }

    def set_decomp(self, decomp: HypercoverDecomposition) -> None:
        """Set the hypercover decomposition for this integration.

        Parameters
        ----------
        decomp : HypercoverDecomposition
            The decomposition to attach.
        """
        self.decomp = decomp
        self.config['decomp_set'] = {
            'timestamp': time.time(),
            'decomp_id': getattr(decomp, 'decomp_id', ''),
        }

    def get_certificates(self) -> list[Certificate]:
        """Return all accumulated certificates.

        Returns
        -------
        list[Certificate]
            A copy of the internal certificate list.
        """
        return list(self._certificates)

    def clear_certificates(self) -> None:
        """Remove all accumulated certificates from internal storage.

        After this call ``self._certificates`` is empty and
        ``get_certificates`` will return an empty list.
        """
        self._certificates.clear()
        self.config['certificates_cleared'] = {'timestamp': time.time()}


@dataclass(slots=True)
class ProjectHypercoverExporter:
    """Exports hypercover decompositions and project sites to external formats.

    Supports JSON, dict, and compact summary formats.
    Theory2.tex §8.1–§8.4.

    Attributes
    ----------
    indent : int
        JSON indentation level. Defaults to 2.
    include_metadata : bool
        Whether to include metadata fields in export. Defaults to True.
    compact : bool
        Whether to emit compact (no-whitespace) JSON. Defaults to False.
    """

    indent: int = 2
    include_metadata: bool = True
    compact: bool = False

    def export_site(self, site: ProjectSite) -> dict[str, Any]:
        """Serialise a ``ProjectSite`` to a plain Python dictionary.

        Extracts all well-known fields from the site using ``getattr`` with
        safe fallbacks, and converts nested objects (coordinates, morphisms)
        to lightweight dicts.

        Parameters
        ----------
        site : ProjectSite
            The project site to export.

        Returns
        -------
        dict[str, Any]
            A structured dict suitable for JSON serialisation.
        """
        site_id = str(getattr(site, 'site_id', '') or str(uuid.uuid4()))
        project_kind = str(getattr(site, 'project_kind', ProjectKind.UNKNOWN) or '')
        name = str(getattr(site, 'name', '') or '')
        description = str(getattr(site, 'description', '') or '')
        created_at = float(getattr(site, 'created_at', 0.0) or 0.0)

        raw_coords = getattr(site, 'coordinates', []) or []
        coords_list: list[dict[str, Any]] = []
        for coord in raw_coords:
            coord_dict: dict[str, Any] = {
                'coord_id': str(getattr(coord, 'coord_id', '') or ''),
                'name': str(getattr(coord, 'name', '') or ''),
                'kind': str(getattr(coord, 'kind', CoordinateKind.UNKNOWN) or ''),
            }
            if self.include_metadata:
                coord_dict['metadata'] = dict(getattr(coord, 'metadata', {}) or {})
            coords_list.append(coord_dict)

        raw_morphisms = getattr(site, 'morphisms', []) or []
        morphisms_list: list[dict[str, Any]] = []
        for morph in raw_morphisms:
            morph_dict: dict[str, Any] = {
                'morphism_id': str(getattr(morph, 'morphism_id', '') or ''),
                'source_id': str(getattr(morph, 'source_id', '') or ''),
                'target_id': str(getattr(morph, 'target_id', '') or ''),
                'kind': str(getattr(morph, 'kind', '') or ''),
            }
            morphisms_list.append(morph_dict)

        result: dict[str, Any] = {
            'site_id': site_id,
            'project_kind': project_kind,
            'name': name,
            'description': description,
            'created_at': created_at,
            'coordinates': coords_list,
            'morphisms': morphisms_list,
        }

        if self.include_metadata:
            result['metadata'] = dict(getattr(site, 'metadata', {}) or {})

        return result

    def export_cover(self, cover: ModuleCover) -> dict[str, Any]:
        """Serialise a ``ModuleCover`` to a plain Python dictionary.

        Parameters
        ----------
        cover : ModuleCover
            The module cover to export.

        Returns
        -------
        dict[str, Any]
            A structured dict ready for JSON serialisation.
        """
        cover_id = str(getattr(cover, 'cover_id', '') or str(uuid.uuid4()))
        site_id = str(getattr(cover, 'site_id', '') or '')
        strategy = str(getattr(cover, 'strategy', CoverStrategy.GREEDY) or '')
        patch_count = int(getattr(cover, 'patch_count', 0) or 0)
        is_admissible = bool(getattr(cover, 'is_admissible', False))
        created_at = float(getattr(cover, 'created_at', 0.0) or 0.0)

        raw_patches = getattr(cover, 'patches', []) or []
        patches_list: list[dict[str, Any]] = []
        for patch in raw_patches:
            if isinstance(patch, dict):
                patches_list.append(patch)
            else:
                patch_dict: dict[str, Any] = {
                    'patch_id': str(getattr(patch, 'patch_id', '') or ''),
                    'coord_ids': list(getattr(patch, 'coord_ids', []) or []),
                }
                if self.include_metadata:
                    patch_dict['metadata'] = dict(getattr(patch, 'metadata', {}) or {})
                patches_list.append(patch_dict)

        raw_metrics = getattr(cover, 'metrics', None)
        metrics_dict: dict[str, Any] = {}
        if raw_metrics is not None:
            if isinstance(raw_metrics, dict):
                metrics_dict = raw_metrics
            else:
                for attr in ('coverage', 'overlap', 'efficiency', 'cost'):
                    val = getattr(raw_metrics, attr, None)
                    if val is not None:
                        metrics_dict[attr] = float(val)

        result: dict[str, Any] = {
            'cover_id': cover_id,
            'site_id': site_id,
            'strategy': strategy,
            'patch_count': patch_count,
            'is_admissible': is_admissible,
            'created_at': created_at,
            'patches': patches_list,
            'metrics': metrics_dict,
        }

        if self.include_metadata:
            result['metadata'] = dict(getattr(cover, 'metadata', {}) or {})

        return result

    def export_decomp(self, decomp: HypercoverDecomposition) -> dict[str, Any]:
        """Serialise a ``HypercoverDecomposition`` to a plain Python dictionary.

        Parameters
        ----------
        decomp : HypercoverDecomposition
            The decomposition to export.

        Returns
        -------
        dict[str, Any]
            Structured dict including all levels, cohomology classes, status,
            and change history.
        """
        decomp_id = str(getattr(decomp, 'decomp_id', '') or str(uuid.uuid4()))
        site_id = str(getattr(decomp, 'site_id', '') or '')
        status = str(getattr(decomp, 'status', DecompositionStatus.PENDING) or '')
        kind = str(getattr(decomp, 'kind', HypercoverKind.CECH) or '')
        created_at = float(getattr(decomp, 'created_at', 0.0) or 0.0)

        raw_levels = getattr(decomp, 'levels', {}) or {}
        levels_dict: dict[str, Any] = {}
        if isinstance(raw_levels, dict):
            for level_key, level_val in raw_levels.items():
                if isinstance(level_val, dict):
                    levels_dict[str(level_key)] = level_val
                else:
                    levels_dict[str(level_key)] = {
                        'level_id': str(getattr(level_val, 'level_id', '') or ''),
                        'index': int(getattr(level_val, 'index', 0) or 0),
                        'nerve': str(getattr(level_val, 'nerve', '') or ''),
                    }

        raw_cohomology = getattr(decomp, 'cohomology_classes', []) or []
        cohomology_list: list[dict[str, Any]] = []
        for cls in raw_cohomology:
            if isinstance(cls, dict):
                cohomology_list.append(cls)
            else:
                cohomology_list.append({
                    'class_id': str(getattr(cls, 'class_id', '') or ''),
                    'degree': int(getattr(cls, 'degree', 0) or 0),
                    'representative': str(getattr(cls, 'representative', '') or ''),
                })

        raw_history = getattr(decomp, 'history', []) or []
        history_list = [str(h) for h in raw_history]

        result: dict[str, Any] = {
            'decomp_id': decomp_id,
            'site_id': site_id,
            'status': status,
            'kind': kind,
            'created_at': created_at,
            'levels': levels_dict,
            'cohomology_classes': cohomology_list,
            'history': history_list,
        }

        if self.include_metadata:
            result['metadata'] = dict(getattr(decomp, 'metadata', {}) or {})

        return result

    def export_fleet(self, fleet: list[FleetMember]) -> list[dict[str, Any]]:
        """Serialise a list of ``FleetMember`` objects to plain dicts.

        Parameters
        ----------
        fleet : list[FleetMember]
            Fleet members to export.

        Returns
        -------
        list[dict[str, Any]]
            One dict per fleet member.
        """
        result: list[dict[str, Any]] = []
        for member in fleet:
            member_dict: dict[str, Any] = {
                'member_id': str(getattr(member, 'member_id', '') or ''),
                'agent_kind': str(getattr(member, 'agent_kind', '') or ''),
                'capabilities': list(getattr(member, 'capabilities', []) or []),
                'trust_level': float(getattr(member, 'trust_level', 0.0) or 0.0),
                'trust_tier': str(getattr(member, 'trust_tier', TrustTier.LOW) or ''),
                'patch_ids': list(getattr(member, 'patch_ids', []) or []),
                'status': str(getattr(member, 'status', FleetStatus.ACTIVE) or ''),
                'max_patches': int(getattr(member, 'max_patches', 0) or 0),
                'description': str(getattr(member, 'description', '') or ''),
            }
            if self.include_metadata:
                member_dict['metadata'] = dict(getattr(member, 'metadata', {}) or {})
            result.append(member_dict)
        return result

    def export_to_json(self, obj: Any) -> str:
        """Route an object to the correct export method and return JSON.

        Detects the type of ``obj`` and delegates to ``export_site``,
        ``export_cover``, ``export_decomp``, or ``export_fleet`` as
        appropriate.

        Parameters
        ----------
        obj : Any
            The object to serialise.

        Returns
        -------
        str
            JSON string representation of the exported object.
        """
        exported: Any

        if isinstance(obj, ProjectSite):
            exported = self.export_site(obj)
        elif isinstance(obj, ModuleCover):
            exported = self.export_cover(obj)
        elif isinstance(obj, HypercoverDecomposition):
            exported = self.export_decomp(obj)
        elif isinstance(obj, list) and all(isinstance(m, FleetMember) for m in obj):
            exported = self.export_fleet(obj)
        elif isinstance(obj, ProjectHypercoverIntegration):
            exported = self.export_full_integration(obj)
        elif isinstance(obj, dict):
            exported = obj
        else:
            exported = {'value': str(obj), 'type': type(obj).__name__}

        if self.compact:
            return json.dumps(exported, separators=(',', ':'), default=str)
        return json.dumps(exported, indent=self.indent, default=str)

    def export_full_integration(
        self, integration: ProjectHypercoverIntegration
    ) -> dict[str, Any]:
        """Export all components of a ``ProjectHypercoverIntegration`` to a dict.

        Parameters
        ----------
        integration : ProjectHypercoverIntegration
            The integration object to export.

        Returns
        -------
        dict[str, Any]
            Complete export including site, cover, decomp, fleet, certs, config.
        """
        result: dict[str, Any] = {
            'export_timestamp': time.time(),
            'export_version': '1.0',
        }

        if integration.site is not None:
            result['site'] = self.export_site(integration.site)
        else:
            result['site'] = None

        if integration.cover is not None:
            result['cover'] = self.export_cover(integration.cover)
        else:
            result['cover'] = None

        if integration.decomp is not None:
            result['decomp'] = self.export_decomp(integration.decomp)
        else:
            result['decomp'] = None

        result['fleet'] = self.export_fleet(integration.fleet)
        result['certificates'] = self.export_certificates(integration.get_certificates())
        result['config'] = dict(integration.config)

        return result

    def export_certificates(
        self, certs: list[Certificate]
    ) -> list[dict[str, Any]]:
        """Serialise a list of ``Certificate`` objects to plain dicts.

        Attempts to use ``cert.serialize()`` or ``cert.project_public()`` if
        available; otherwise extracts fields via ``getattr``.

        Parameters
        ----------
        certs : list[Certificate]
            Certificates to export.

        Returns
        -------
        list[dict[str, Any]]
            One dict per certificate.
        """
        result: list[dict[str, Any]] = []
        for cert in certs:
            cert_dict: dict[str, Any]
            if hasattr(cert, 'serialize') and callable(cert.serialize):
                try:
                    serialized = cert.serialize()
                    cert_dict = serialized if isinstance(serialized, dict) else {'raw': str(serialized)}
                except Exception:
                    cert_dict = {}
            elif hasattr(cert, 'project_public') and callable(cert.project_public):
                try:
                    cert_dict = cert.project_public()
                except Exception:
                    cert_dict = {}
            else:
                cert_dict = {}

            if not cert_dict:
                cert_dict = {
                    'certificate_id': str(getattr(cert, 'certificate_id', '') or ''),
                    'coord_id': str(getattr(cert, 'coord_id', '') or getattr(cert, 'coordinate_id', '') or ''),
                    'trust_level': float(getattr(cert, 'trust_level', 0.0) or 0.0),
                    'status': str(getattr(cert, 'status', CertificateStatus.PENDING) or ''),
                    'verified_propositions': list(getattr(cert, 'verified_propositions', []) or []),
                    'issuer': str(getattr(cert, 'issuer', '') or ''),
                    'issued_at': float(getattr(cert, 'issued_at', 0.0) or 0.0),
                }
                if self.include_metadata:
                    cert_dict['metadata'] = dict(getattr(cert, 'metadata', {}) or {})

            result.append(cert_dict)
        return result

    def to_file(self, path: str | pathlib.Path, obj: Any) -> None:
        """Write the JSON export of ``obj`` to a file at ``path``.

        Creates parent directories if they do not exist.

        Parameters
        ----------
        path : str or pathlib.Path
            Destination file path.
        obj : Any
            Object to export and write.
        """
        file_path = pathlib.Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        json_str = self.export_to_json(obj)
        file_path.write_text(json_str, encoding='utf-8')


@dataclass(slots=True)
class ProjectHypercoverImporter:
    """Imports project sites, covers, and decompositions from JSON or dict format.

    Theory2.tex §8.1–§8.4.

    Attributes
    ----------
    strict : bool
        If True, raise on missing required fields. Defaults to False.
    """

    strict: bool = False

    def import_site(self, data: dict[str, Any]) -> ProjectSite:
        """Parse a dict and construct a ``ProjectSite``.

        Parameters
        ----------
        data : dict[str, Any]
            Serialised project site data.

        Returns
        -------
        ProjectSite
            The reconstructed project site.

        Raises
        ------
        ValueError
            If ``strict`` is True and required fields are missing.
        """
        errors = self.validate_import(data, 'site')
        if errors and self.strict:
            raise ValueError(f'Missing required site fields: {errors}')

        site_id = str(data.get('site_id', '') or str(uuid.uuid4()))
        name = str(data.get('name', '') or '')
        description = str(data.get('description', '') or '')
        created_at = float(data.get('created_at', time.time()) or time.time())
        metadata = dict(data.get('metadata', {}) or {})

        kind_str = str(data.get('project_kind', '') or '').upper()
        try:
            project_kind = ProjectKind[kind_str]
        except KeyError:
            project_kind = ProjectKind.UNKNOWN

        raw_coords = data.get('coordinates', []) or []
        coordinates: list[CoordinateObject] = []
        for cd in raw_coords:
            if not isinstance(cd, dict):
                continue
            coord_kind_str = str(cd.get('kind', '') or '').upper()
            try:
                coord_kind = CoordinateKind[coord_kind_str]
            except KeyError:
                coord_kind = CoordinateKind.UNKNOWN
            try:
                coord = CoordinateObject(
                    coord_id=str(cd.get('coord_id', '') or str(uuid.uuid4())),
                    name=str(cd.get('name', '') or ''),
                    kind=coord_kind,
                    metadata=dict(cd.get('metadata', {}) or {}),
                )
                coordinates.append(coord)
            except TypeError:
                pass

        raw_morphisms = data.get('morphisms', []) or []
        morphisms: list[CoordinateMorphism] = []
        for md in raw_morphisms:
            if not isinstance(md, dict):
                continue
            try:
                morph = CoordinateMorphism(
                    morphism_id=str(md.get('morphism_id', '') or str(uuid.uuid4())),
                    source_id=str(md.get('source_id', '') or ''),
                    target_id=str(md.get('target_id', '') or ''),
                    kind=str(md.get('kind', '') or ''),
                )
                morphisms.append(morph)
            except TypeError:
                pass

        try:
            site = ProjectSite(
                site_id=site_id,
                name=name,
                description=description,
                project_kind=project_kind,
                coordinates=coordinates,
                morphisms=morphisms,
                created_at=created_at,
                metadata=metadata,
            )
        except TypeError:
            site = ProjectSite(
                site_id=site_id,
                name=name,
                project_kind=project_kind,
            )

        return site

    def import_cover(self, data: dict[str, Any]) -> ModuleCover:
        """Parse a dict and construct a ``ModuleCover``.

        Parameters
        ----------
        data : dict[str, Any]
            Serialised module cover data.

        Returns
        -------
        ModuleCover
            The reconstructed module cover.

        Raises
        ------
        ValueError
            If ``strict`` is True and required fields are missing.
        """
        errors = self.validate_import(data, 'cover')
        if errors and self.strict:
            raise ValueError(f'Missing required cover fields: {errors}')

        cover_id = str(data.get('cover_id', '') or str(uuid.uuid4()))
        site_id = str(data.get('site_id', '') or '')
        patch_count = int(data.get('patch_count', 0) or 0)
        is_admissible = bool(data.get('is_admissible', False))
        created_at = float(data.get('created_at', time.time()) or time.time())
        metadata = dict(data.get('metadata', {}) or {})

        strategy_str = str(data.get('strategy', '') or '').upper()
        try:
            strategy = CoverStrategy[strategy_str]
        except KeyError:
            strategy = CoverStrategy.GREEDY

        patches = list(data.get('patches', []) or [])
        metrics_raw = data.get('metrics', {}) or {}
        try:
            metrics = CoverMetric(**metrics_raw) if metrics_raw else None
        except TypeError:
            metrics = None

        try:
            cover = ModuleCover(
                cover_id=cover_id,
                site_id=site_id,
                strategy=strategy,
                patch_count=patch_count,
                is_admissible=is_admissible,
                patches=patches,
                metrics=metrics,
                created_at=created_at,
                metadata=metadata,
            )
        except TypeError:
            cover = ModuleCover(
                cover_id=cover_id,
                site_id=site_id,
                strategy=strategy,
                patches=patches,
            )

        return cover

    def import_decomp(self, data: dict[str, Any]) -> HypercoverDecomposition:
        """Parse a dict and construct a ``HypercoverDecomposition``.

        Parameters
        ----------
        data : dict[str, Any]
            Serialised hypercover decomposition data.

        Returns
        -------
        HypercoverDecomposition
            The reconstructed decomposition.

        Raises
        ------
        ValueError
            If ``strict`` is True and required fields are missing.
        """
        errors = self.validate_import(data, 'decomp')
        if errors and self.strict:
            raise ValueError(f'Missing required decomp fields: {errors}')

        decomp_id = str(data.get('decomp_id', '') or str(uuid.uuid4()))
        site_id = str(data.get('site_id', '') or '')
        created_at = float(data.get('created_at', time.time()) or time.time())
        metadata = dict(data.get('metadata', {}) or {})
        history = list(data.get('history', []) or [])

        status_str = str(data.get('status', '') or '').upper()
        try:
            status = DecompositionStatus[status_str]
        except KeyError:
            status = DecompositionStatus.PENDING

        kind_str = str(data.get('kind', '') or '').upper()
        try:
            kind = HypercoverKind[kind_str]
        except KeyError:
            kind = HypercoverKind.CECH

        raw_levels = data.get('levels', {}) or {}
        levels: dict[str, HypercoverLevel] = {}
        for lk, lv in raw_levels.items():
            if isinstance(lv, dict):
                try:
                    lvl = HypercoverLevel(
                        level_id=str(lv.get('level_id', '') or str(uuid.uuid4())),
                        index=int(lv.get('index', 0) or 0),
                        nerve=lv.get('nerve'),
                    )
                    levels[str(lk)] = lvl
                except TypeError:
                    levels[str(lk)] = lv

        raw_cohomology = data.get('cohomology_classes', []) or []
        cohomology_classes: list[CohomologyClass] = []
        for cls in raw_cohomology:
            if isinstance(cls, dict):
                try:
                    cc = CohomologyClass(
                        class_id=str(cls.get('class_id', '') or str(uuid.uuid4())),
                        degree=int(cls.get('degree', 0) or 0),
                        representative=str(cls.get('representative', '') or ''),
                    )
                    cohomology_classes.append(cc)
                except TypeError:
                    pass

        try:
            decomp = HypercoverDecomposition(
                decomp_id=decomp_id,
                site_id=site_id,
                status=status,
                kind=kind,
                levels=levels,
                cohomology_classes=cohomology_classes,
                history=history,
                created_at=created_at,
                metadata=metadata,
            )
        except TypeError:
            decomp = HypercoverDecomposition(
                decomp_id=decomp_id,
                site_id=site_id,
                status=status,
            )

        return decomp

    def import_fleet(
        self, data: list[dict[str, Any]]
    ) -> list[FleetMember]:
        """Parse a list of dicts and construct a list of ``FleetMember`` objects.

        Parameters
        ----------
        data : list[dict[str, Any]]
            Serialised fleet member dicts.

        Returns
        -------
        list[FleetMember]
            Reconstructed fleet members.
        """
        members: list[FleetMember] = []
        for entry in data:
            errors = self.validate_import(entry, 'fleet_member')
            if errors and self.strict:
                raise ValueError(f'Missing fleet member fields: {errors}')

            member_id = str(entry.get('member_id', '') or str(uuid.uuid4()))
            agent_kind = str(entry.get('agent_kind', 'generic') or 'generic')
            capabilities = list(entry.get('capabilities', []) or [])
            trust_level = float(entry.get('trust_level', 0.5) or 0.5)
            patch_ids = list(entry.get('patch_ids', []) or [])
            max_patches = int(entry.get('max_patches', 10) or 10)
            description = str(entry.get('description', '') or '')
            metadata = dict(entry.get('metadata', {}) or {})

            tier_str = str(entry.get('trust_tier', '') or '').upper()
            try:
                trust_tier = TrustTier[tier_str]
            except KeyError:
                trust_tier = TrustTier.MEDIUM if trust_level >= 0.5 else TrustTier.LOW

            status_str = str(entry.get('status', 'ACTIVE') or 'ACTIVE').upper()
            try:
                status = FleetStatus[status_str]
            except KeyError:
                status = FleetStatus.ACTIVE

            try:
                member = FleetMember(
                    member_id=member_id,
                    agent_kind=agent_kind,
                    capabilities=capabilities,
                    trust_level=trust_level,
                    trust_tier=trust_tier,
                    patch_ids=patch_ids,
                    status=status,
                    max_patches=max_patches,
                    description=description,
                    metadata=metadata,
                )
            except TypeError:
                member = FleetMember(
                    member_id=member_id,
                    agent_kind=agent_kind,
                    capabilities=capabilities,
                    trust_level=trust_level,
                    patch_ids=patch_ids,
                )

            members.append(member)

        return members

    def import_from_json(self, json_str: str) -> dict[str, Any]:
        """Parse a JSON string and detect its top-level type.

        Parameters
        ----------
        json_str : str
            JSON-encoded string of an exported object.

        Returns
        -------
        dict[str, Any]
            Parsed dict, with a ``'_detected_type'`` key indicating
            ``'site'``, ``'cover'``, ``'decomp'``, ``'fleet'``, or
            ``'integration'``.
        """
        parsed = json.loads(json_str)
        if not isinstance(parsed, dict):
            return {'_detected_type': 'unknown', 'value': parsed}

        detected: str
        if 'site_id' in parsed and 'coordinates' in parsed:
            detected = 'site'
        elif 'cover_id' in parsed and 'patches' in parsed:
            detected = 'cover'
        elif 'decomp_id' in parsed and 'levels' in parsed:
            detected = 'decomp'
        elif 'site' in parsed and 'cover' in parsed and 'fleet' in parsed:
            detected = 'integration'
        elif isinstance(parsed.get('value'), list):
            detected = 'fleet'
        else:
            detected = 'unknown'

        parsed['_detected_type'] = detected
        return parsed

    def from_file(self, path: str | pathlib.Path) -> dict[str, Any]:
        """Read a JSON file and return the parsed dict with type detection.

        Parameters
        ----------
        path : str or pathlib.Path
            Path to the JSON file.

        Returns
        -------
        dict[str, Any]
            Parsed content with ``'_detected_type'`` annotation.
        """
        file_path = pathlib.Path(path)
        json_str = file_path.read_text(encoding='utf-8')
        return self.import_from_json(json_str)

    def validate_import(
        self, data: dict[str, Any], kind: str
    ) -> list[str]:
        """Return a list of missing or invalid fields for the given kind.

        Parameters
        ----------
        data : dict[str, Any]
            The data dict to validate.
        kind : str
            One of ``'site'``, ``'cover'``, ``'decomp'``, ``'fleet_member'``.

        Returns
        -------
        list[str]
            Names of missing or invalid fields; empty if all required fields
            are present.
        """
        required_fields: dict[str, list[str]] = {
            'site': ['site_id', 'project_kind'],
            'cover': ['cover_id', 'site_id', 'strategy'],
            'decomp': ['decomp_id', 'site_id', 'status'],
            'fleet_member': ['member_id', 'agent_kind', 'trust_level'],
        }
        fields = required_fields.get(kind, [])
        missing: list[str] = []
        for f in fields:
            val = data.get(f, None)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(f)
        return missing


def register_project_site(
    site: ProjectSite,
    registry: dict[str, ProjectSite] | None = None,
) -> str:
    """Register a ``ProjectSite`` in the module-level (or provided) registry.

    Parameters
    ----------
    site : ProjectSite
        The site to register.
    registry : dict[str, ProjectSite] or None, optional
        Custom registry dict; defaults to the module-level ``_SITE_REGISTRY``.

    Returns
    -------
    str
        The ``site_id`` under which the site was registered.
    """
    target = registry if registry is not None else _SITE_REGISTRY
    site_id = str(getattr(site, 'site_id', '') or str(uuid.uuid4()))
    target[site_id] = site
    return site_id


def connect_fleet_to_judgment_system(
    fleet: list[FleetMember],
    judgments: list[JudgmentTerm],
) -> dict[str, list[str]]:
    """Match fleet members' assigned patches to judgment terms.

    For each ``FleetMember`` in ``fleet``, the function checks each of its
    assigned patch IDs against the formula or term identifier of every
    ``JudgmentTerm`` in ``judgments``.  Both directions of partial string
    matching are tested so that loosely named patches can be associated with
    broad judgment formulas.

    Parameters
    ----------
    fleet : list[FleetMember]
        Fleet members to connect.
    judgments : list[JudgmentTerm]
        Judgment terms to match against.

    Returns
    -------
    dict[str, list[str]]
        Mapping from fleet member ID to a list of matched judgment term strings.
    """
    judgment_strings: list[str] = []
    for jt in judgments:
        if hasattr(jt, 'term_id') and jt.term_id:
            judgment_strings.append(str(jt.term_id))
        elif hasattr(jt, 'formula') and jt.formula:
            judgment_strings.append(str(jt.formula))
        else:
            judgment_strings.append(str(jt))

    result: dict[str, list[str]] = {}

    for member in fleet:
        member_id = str(getattr(member, 'member_id', '') or str(id(member)))
        patch_ids = [str(p) for p in (getattr(member, 'patch_ids', []) or [])]
        matched_judgments: list[str] = []

        for patch_id in patch_ids:
            for jstr in judgment_strings:
                if patch_id == jstr or patch_id in jstr or jstr in patch_id:
                    if jstr not in matched_judgments:
                        matched_judgments.append(jstr)

        result[member_id] = matched_judgments

    return result


def load_integration_from_config(
    config_path: str,
) -> ProjectHypercoverIntegration:
    """Load a ``ProjectHypercoverIntegration`` from a JSON config file.

    Reads the JSON file at ``config_path``, uses a ``ProjectHypercoverImporter``
    to reconstruct site, cover, decomp, and fleet if present, and returns a
    fully configured ``ProjectHypercoverIntegration``.

    Parameters
    ----------
    config_path : str
        Path to the JSON configuration file.

    Returns
    -------
    ProjectHypercoverIntegration
        Populated integration object.
    """
    path = pathlib.Path(config_path)
    raw_text = path.read_text(encoding='utf-8')
    config_data: dict[str, Any] = json.loads(raw_text)

    importer = ProjectHypercoverImporter(strict=False)
    integration = ProjectHypercoverIntegration()

    if 'site' in config_data and isinstance(config_data['site'], dict):
        try:
            site = importer.import_site(config_data['site'])
            integration.set_site(site)
        except Exception:
            pass

    if 'cover' in config_data and isinstance(config_data['cover'], dict):
        try:
            cover = importer.import_cover(config_data['cover'])
            integration.set_cover(cover)
        except Exception:
            pass

    if 'decomp' in config_data and isinstance(config_data['decomp'], dict):
        try:
            decomp = importer.import_decomp(config_data['decomp'])
            integration.set_decomp(decomp)
        except Exception:
            pass

    if 'fleet' in config_data and isinstance(config_data['fleet'], list):
        try:
            fleet = importer.import_fleet(config_data['fleet'])
            integration.fleet = fleet
        except Exception:
            pass

    extra_config = {
        k: v for k, v in config_data.items()
        if k not in ('site', 'cover', 'decomp', 'fleet', 'certificates')
    }
    integration.config.update(extra_config)
    integration.config['loaded_from'] = str(path.resolve())
    integration.config['load_timestamp'] = time.time()

    return integration


def create_default_integration() -> ProjectHypercoverIntegration:
    """Create a ``ProjectHypercoverIntegration`` with default empty values.

    Useful as a starting point when no existing configuration is available.

    Returns
    -------
    ProjectHypercoverIntegration
        A freshly initialised integration object with all fields at defaults.
    """
    integration = ProjectHypercoverIntegration()
    integration.config['created_by'] = 'create_default_integration'
    integration.config['created_at'] = time.time()
    integration.config['integration_id'] = str(uuid.uuid4())
    return integration


# copilot: integration module — ProjectHypercoverIntegration, Exporter, Importer,
# register_project_site, connect_fleet_to_judgment_system bridge project_hypercovers
# to the JuGeo trust stack for LLM-orchestrated verification workflows.
