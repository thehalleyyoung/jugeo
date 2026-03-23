"""Package-manifest authority surfaces for the shared JuGeo core.

This module turns the package manifest into a first-class semantic record for
shared JuGeo.  The authoritative semantic source is
``preliminaries/theory2.tex``.  The JSON files
``theory2-src-blueprint.json`` and ``theory2-generation-order.json`` are used
only as structural hints about file ordering and stage names.  That policy is
represented explicitly in the exported provenance constants and in the
``PackageManifest`` record itself so callers can inspect where the semantics are
coming from and what is merely organizational scaffolding.

The guiding theory claims reflected here are deliberately concrete:

* subsystem manifests are explicit and stable rather than inferred from import
  accidents;
* capability surfaces are public projections of real subsystem commitments;
* dependency ordering is canonical, deterministic, and challengeable;
* scope honesty forbids a subsystem from claiming closure outside its declared
  support and dependency frontier; and
* authority boundaries stay stable across serialization and reload so proposal
  channels, including the bounded copilot proposal route, never silently become
  settlement authority.

The resulting manifest is intentionally readable by humans and language models.
It is also intentionally conservative: when the module cannot justify a stronger
claim from the theory-shaped record, it declines to synthesize one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Iterable, Mapping

from .errors import FailureScope, JuGeoError, raise_with_scope
from .runtime_defaults import PolicyPreset, RuntimeDefaults, default_runtime_options

ROOT_STAGE: Final[str] = 'root-foundation'
ROOT_SEQUENCE: Final[int] = 3

MANIFEST_SPEC_PROVENANCE: Final[Mapping[str, str | int]] = MappingProxyType(
    {
        'semantic_source': 'preliminaries/theory2.tex',
        'semantic_source_role': 'authoritative-semantic-source',
        'structural_blueprint': 'theory2-src-blueprint.json',
        'structural_generation_order': 'theory2-generation-order.json',
        'structural_hint_role': 'structure-only',
        'target_file': 'src/jugeo/package_manifest.py',
        'target_test': 'tests/jugeo/test_package_manifest.py',
        'stage': ROOT_STAGE,
        'sequence': ROOT_SEQUENCE,
    }
)

THEOREM_TARGETS: Final[tuple[str, ...]] = (
    'serialization determinism',
    'dependency-trace integrity',
    'stale-manifest conservativity',
    'projection faithfulness',
    'scope honesty',
    'jurisdiction soundness',
    'escalation honesty',
)


class CapabilityFlag(str, Enum):
    """Named capability surfaces exported by the shared package manifest."""

    CONFIGURATION = 'configuration'
    AUTHORITY = 'authority'
    GEOMETRY = 'geometry'
    JUDGMENTS = 'judgments'
    EVIDENCE = 'evidence'
    COPILOT_PROPOSALS = 'copilot-proposals'
    PACKS = 'packs'
    SOLVER = 'solver'
    RUNTIME = 'runtime'
    GENERATION = 'generation'
    ORCHESTRATION = 'orchestration'
    IDEATION = 'ideation'
    INTERFACES = 'interfaces'


@dataclass(frozen=True, slots=True)
class ManifestCapability:
    """Public capability projection for one named surface.

    ``ManifestCapability`` is intentionally not merely a boolean feature flag.
    Theory2 makes capability reporting part of public honesty: a surface can be
    listed only when JuGeo can also say which subsystem exposes it, which stage
    stabilizes it, and which authority boundary keeps the claim from widening
    into something stronger than the manifest can justify.
    """

    name: str
    enabled: bool
    stage: str
    rationale: str
    surfaced_by: tuple[str, ...] = ()
    authority_boundary: str = ''
    honest_scope: str = ''

    def __post_init__(self) -> None:
        object.__setattr__(self, 'name', _normalize_required_text(self.name, field_name='name'))
        object.__setattr__(self, 'stage', _normalize_required_text(self.stage, field_name='stage'))
        object.__setattr__(self, 'rationale', _normalize_required_text(self.rationale, field_name='rationale'))
        object.__setattr__(self, 'authority_boundary', _normalize_required_text(self.authority_boundary, field_name='authority_boundary'))
        object.__setattr__(self, 'honest_scope', _normalize_required_text(self.honest_scope, field_name='honest_scope'))
        object.__setattr__(self, 'surfaced_by', _normalize_text_tuple(self.surfaced_by, field_name='surfaced_by'))
        if self.enabled and not self.surfaced_by:
            raise ValueError('enabled capabilities must identify at least one exporting subsystem.')

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'enabled': self.enabled,
            'stage': self.stage,
            'rationale': self.rationale,
            'surfaced_by': list(self.surfaced_by),
            'authority_boundary': self.authority_boundary,
            'honest_scope': self.honest_scope,
        }


@dataclass(frozen=True, slots=True)
class SubsystemManifest:
    """Stable declaration of one shared JuGeo subsystem.

    The record intentionally mirrors the theory-shaped concerns rather than a
    minimal import graph.  Every subsystem names its package path, visible
    capabilities, dependency frontier, stable exports, residual exports,
    evidence channels, public surfaces, stage, scope-honesty rule, and
    authority boundary.
    """

    name: str
    package: str
    capabilities: tuple[CapabilityFlag, ...]
    dependencies: tuple[str, ...] = ()
    stage: str = ROOT_STAGE
    authority_boundary: str = ''
    scope_honesty: str = ''
    stable_exports: tuple[str, ...] = ()
    residual_exports: tuple[str, ...] = ()
    evidence_channels: tuple[str, ...] = ()
    public_surfaces: tuple[str, ...] = ()
    generation_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'name', _normalize_required_text(self.name, field_name='name'))
        object.__setattr__(self, 'package', _normalize_required_text(self.package, field_name='package'))
        object.__setattr__(self, 'stage', _normalize_required_text(self.stage, field_name='stage'))
        object.__setattr__(self, 'authority_boundary', _normalize_required_text(self.authority_boundary, field_name='authority_boundary'))
        object.__setattr__(self, 'scope_honesty', _normalize_required_text(self.scope_honesty, field_name='scope_honesty'))
        object.__setattr__(self, 'capabilities', _normalize_capabilities(self.capabilities))
        object.__setattr__(self, 'dependencies', _normalize_text_tuple(self.dependencies, field_name='dependencies'))
        object.__setattr__(self, 'stable_exports', _normalize_text_tuple(self.stable_exports, field_name='stable_exports'))
        object.__setattr__(self, 'residual_exports', _normalize_text_tuple(self.residual_exports, field_name='residual_exports'))
        object.__setattr__(self, 'evidence_channels', _normalize_text_tuple(self.evidence_channels, field_name='evidence_channels'))
        object.__setattr__(self, 'public_surfaces', _normalize_text_tuple(self.public_surfaces, field_name='public_surfaces'))
        object.__setattr__(self, 'generation_notes', _normalize_text_tuple(self.generation_notes, field_name='generation_notes'))
        if not self.package.startswith('jugeo'):
            raise ValueError('subsystem packages must stay within the jugeo namespace.')
        if self.name in self.dependencies:
            raise ValueError('subsystems may not depend on themselves.')
        if not self.stable_exports:
            raise ValueError('subsystems must publish at least one stable export.')
        if not self.evidence_channels:
            raise ValueError('subsystems must declare evidence channels or reporting routes.')
        if CapabilityFlag.COPILOT_PROPOSALS in self.capabilities and CapabilityFlag.AUTHORITY in self.capabilities:
            raise ValueError('copilot proposal surfaces may not silently become authority surfaces.')

    def declares(self, capability: CapabilityFlag | str) -> bool:
        candidate = capability if isinstance(capability, CapabilityFlag) else CapabilityFlag(capability)
        return candidate in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'package': self.package,
            'capabilities': [capability.value for capability in self.capabilities],
            'dependencies': list(self.dependencies),
            'stage': self.stage,
            'authority_boundary': self.authority_boundary,
            'scope_honesty': self.scope_honesty,
            'stable_exports': list(self.stable_exports),
            'residual_exports': list(self.residual_exports),
            'evidence_channels': list(self.evidence_channels),
            'public_surfaces': list(self.public_surfaces),
            'generation_notes': list(self.generation_notes),
        }


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """Canonical manifest for the shared JuGeo package.

    ``PackageManifest`` is the package-wide public projection of the subsystem
    registry.  It keeps the dependency order stable, exposes theorem targets,
    names the authoritative semantic source, and marks JSON files as structural
    hints only.  The properties are intentionally shaped so neighboring modules
    such as the CLI and configuration loader can render the manifest without
    reconstructing the theory-level distinctions themselves.
    """

    name: str
    version: str
    defaults: RuntimeDefaults
    subsystems: tuple[SubsystemManifest, ...] = field(default_factory=tuple)
    format_version: str = '1.0'
    semantic_source: str = MANIFEST_SPEC_PROVENANCE['semantic_source']
    semantic_source_role: str = MANIFEST_SPEC_PROVENANCE['semantic_source_role']
    structural_hints: tuple[str, ...] = (
        str(MANIFEST_SPEC_PROVENANCE['structural_blueprint']),
        str(MANIFEST_SPEC_PROVENANCE['structural_generation_order']),
    )
    structural_hint_role: str = str(MANIFEST_SPEC_PROVENANCE['structural_hint_role'])
    theorem_targets: tuple[str, ...] = THEOREM_TARGETS

    def __post_init__(self) -> None:
        object.__setattr__(self, 'name', _normalize_required_text(self.name, field_name='name'))
        object.__setattr__(self, 'version', _normalize_required_text(self.version, field_name='version'))
        object.__setattr__(self, 'format_version', _normalize_required_text(self.format_version, field_name='format_version'))
        object.__setattr__(self, 'semantic_source', _normalize_required_text(self.semantic_source, field_name='semantic_source'))
        object.__setattr__(self, 'semantic_source_role', _normalize_required_text(self.semantic_source_role, field_name='semantic_source_role'))
        object.__setattr__(self, 'structural_hints', _normalize_text_tuple(self.structural_hints, field_name='structural_hints'))
        object.__setattr__(self, 'structural_hint_role', _normalize_required_text(self.structural_hint_role, field_name='structural_hint_role'))
        object.__setattr__(self, 'theorem_targets', _normalize_text_tuple(self.theorem_targets, field_name='theorem_targets'))
        object.__setattr__(self, 'subsystems', _normalize_subsystems(self.subsystems))
        if self.semantic_source_role != 'authoritative-semantic-source':
            raise ValueError('semantic_source_role must keep theory2.tex authoritative.')
        if self.structural_hint_role != 'structure-only':
            raise ValueError('structural hints must remain explicitly non-authoritative.')
        if not self.subsystems:
            raise ValueError('package manifests must contain at least one subsystem.')

    @property
    def package_name(self) -> str:
        return self.name

    @property
    def subsystem_order(self) -> tuple[str, ...]:
        return tuple(subsystem.name for subsystem in self.subsystems)

    def subsystem_names(self) -> tuple[str, ...]:
        return self.subsystem_order

    @property
    def subsystem_map(self) -> Mapping[str, SubsystemManifest]:
        return MappingProxyType({subsystem.name: subsystem for subsystem in self.subsystems})

    @property
    def capabilities(self) -> Mapping[str, ManifestCapability]:
        return _build_capability_surfaces(self.subsystems)

    @property
    def dependency_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for subsystem in self.subsystems:
            for dependency in subsystem.dependencies:
                pairs.append((dependency, subsystem.name))
        return tuple(pairs)

    @property
    def authority_boundaries(self) -> Mapping[str, str]:
        return MappingProxyType({subsystem.name: subsystem.authority_boundary for subsystem in self.subsystems})

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'package_name': self.package_name,
            'version': self.version,
            'format_version': self.format_version,
            'defaults': self.defaults.get_all(),
            'semantic_source': self.semantic_source,
            'semantic_source_role': self.semantic_source_role,
            'structural_hints': list(self.structural_hints),
            'structural_hint_role': self.structural_hint_role,
            'theorem_targets': list(self.theorem_targets),
            'subsystem_order': list(self.subsystem_order),
            'subsystems': [subsystem.to_dict() for subsystem in self.subsystems],
            'capabilities': {
                name: capability.to_dict()
                for name, capability in self.capabilities.items()
            },
        }


def enumerate_subsystems(manifest: PackageManifest) -> tuple[str, ...]:
    """Return the canonical subsystem order.

    The order is meaningful: it reflects the dependency-oriented shared
    foundation wave implied by theory2 and reinforced by the generation-order
    structure file.
    """

    return manifest.subsystem_names()


def validate_manifest_shape(payload: Mapping[str, Any]) -> None:
    """Validate the public JSON-compatible shape of a package manifest.

    This validation is intentionally stronger than a shallow required-field
    check.  It enforces the public theory commitments that neighboring code
    relies on: canonical ordering, capability-surface metadata, explicit source
    authority, and subsystem-level honesty/boundary fields.
    """

    required = {
        'name',
        'package_name',
        'version',
        'format_version',
        'defaults',
        'semantic_source',
        'semantic_source_role',
        'structural_hints',
        'structural_hint_role',
        'theorem_targets',
        'subsystem_order',
        'subsystems',
        'capabilities',
    }
    missing = sorted(required.difference(payload))
    if missing:
        _raise_manifest_error(
            code='manifest-missing-fields',
            summary='Manifest payload is missing required fields.',
            details={'missing': missing},
        )

    if not isinstance(payload['defaults'], Mapping):
        _raise_manifest_error(
            code='manifest-invalid-defaults',
            summary='Manifest defaults must be a mapping.',
            details={'type': type(payload['defaults']).__name__},
        )

    subsystems = payload['subsystems']
    if not _is_nonstring_iterable(subsystems):
        _raise_manifest_error(
            code='manifest-invalid-subsystems',
            summary='Manifest subsystems must be an iterable of mappings.',
            details={'type': type(subsystems).__name__},
        )

    subsystem_order = payload['subsystem_order']
    if not _is_nonstring_iterable(subsystem_order):
        _raise_manifest_error(
            code='manifest-invalid-subsystem-order',
            summary='Manifest subsystem order must be an iterable of names.',
            details={'type': type(subsystem_order).__name__},
        )

    normalized_order = tuple(_normalize_required_text(name, field_name='subsystem_order item') for name in subsystem_order)
    normalized_subsystems = tuple(subsystems)
    if len(normalized_subsystems) != len(normalized_order):
        _raise_manifest_error(
            code='manifest-order-mismatch',
            summary='Manifest subsystem order length does not match subsystem entries.',
            details={'subsystems': len(normalized_subsystems), 'subsystem_order': len(normalized_order)},
        )

    seen_names: set[str] = set()
    for index, subsystem in enumerate(normalized_subsystems):
        if not isinstance(subsystem, Mapping):
            _raise_manifest_error(
                code='manifest-invalid-subsystem-entry',
                summary='Each subsystem entry must be a mapping.',
                details={'index': index, 'type': type(subsystem).__name__},
            )
        _validate_subsystem_payload(subsystem, index=index)
        subsystem_name = str(subsystem['name'])
        if subsystem_name != normalized_order[index]:
            _raise_manifest_error(
                code='manifest-subsystem-ordering-error',
                summary='Subsystem order must match the serialized subsystem list.',
                details={'index': index, 'order_name': normalized_order[index], 'subsystem_name': subsystem_name},
            )
        dependencies = subsystem['dependencies']
        if not _is_nonstring_iterable(dependencies):
            _raise_manifest_error(
                code='manifest-invalid-dependency-list',
                summary='Subsystem dependencies must be an iterable of names.',
                details={'index': index, 'type': type(dependencies).__name__},
            )
        for dependency in dependencies:
            dependency_name = _normalize_required_text(dependency, field_name='dependency')
            if dependency_name not in seen_names:
                _raise_manifest_error(
                    code='manifest-dependency-order-violation',
                    summary='Dependencies must name previously declared subsystems.',
                    details={'subsystem': subsystem_name, 'dependency': dependency_name},
                )
        seen_names.add(subsystem_name)

    capabilities = payload['capabilities']
    if not isinstance(capabilities, Mapping):
        _raise_manifest_error(
            code='manifest-invalid-capabilities',
            summary='Manifest capabilities must be a mapping of capability payloads.',
            details={'type': type(capabilities).__name__},
        )
    if not capabilities:
        _raise_manifest_error(
            code='manifest-empty-capabilities',
            summary='Manifest capabilities may not be empty.',
            details={},
        )
    for capability_name, capability_payload in capabilities.items():
        _validate_capability_payload(capability_name, capability_payload)


def build_package_manifest(
    *,
    version: str = '0.1.0',
    preset: PolicyPreset = PolicyPreset.BALANCED,
) -> PackageManifest:
    """Build the canonical shared-package manifest.

    The subsystem order is stable across presets.  Policy presets affect only
    runtime defaults, not what the package claims to contain or how trust and
    dependency boundaries are ordered.
    """

    manifest = PackageManifest(
        name='jugeo',
        version=version,
        defaults=default_runtime_options(preset),
        subsystems=_build_canonical_subsystems(),
    )
    _validate_package_manifest(manifest)
    validate_manifest_shape(manifest.to_dict())
    return manifest


def _build_canonical_subsystems() -> tuple[SubsystemManifest, ...]:
    return (
        SubsystemManifest(
            name='kernel',
            package='jugeo.kernel',
            capabilities=(CapabilityFlag.CONFIGURATION, CapabilityFlag.AUTHORITY),
            dependencies=(),
            stage='shared-kernel',
            authority_boundary=(
                'Defines configuration defaults and authority ceilings for later subsystems, '
                'but it does not certify domain facts on their behalf.'
            ),
            scope_honesty=(
                'Publishes policy and delegation boundaries only for the shared core; it does not '
                'pretend kernel policy settles geometry, evidence, or interface claims.'
            ),
            stable_exports=(
                'configuration layering',
                'authority centers',
                'delegation validation',
                'lifecycle boundaries',
                'health reporting roots',
            ),
            residual_exports=(
                'project-specific service graphs remain downstream',
                'runtime boot wiring is intentionally outside the manifest',
            ),
            evidence_channels=('runtime', 'human-review'),
            public_surfaces=('configuration', 'health', 'authority diagnostics'),
            generation_notes=(
                'Structural hint: shared-kernel appears immediately after the root manifest wave.',
                'Semantic authority remains theory2.tex rather than the generation-order JSON.',
            ),
        ),
        SubsystemManifest(
            name='geometry',
            package='jugeo.geometry',
            capabilities=(CapabilityFlag.GEOMETRY,),
            dependencies=('kernel',),
            stage='shared-geometry',
            authority_boundary=(
                'Owns sites, covers, supports, overlaps, and descent primitives, but it does not by itself '
                'declare that any local family has glued into a settled global artifact.'
            ),
            scope_honesty=(
                'Can describe decomposition and locality boundaries, yet any claim of global closure still '
                'depends on downstream judgment and evidence layers.'
            ),
            stable_exports=(
                'semantic coordinates',
                'covers and hypercovers',
                'support regions',
                'descent and gluing primitives',
            ),
            residual_exports=(
                'project-specific cover refinement heuristics remain open',
                'higher-overlap treaty specializations remain downstream',
            ),
            evidence_channels=('proof', 'runtime'),
            public_surfaces=('descent diagnostics', 'support-local reopening hooks'),
            generation_notes=(
                'Structural hint: shared-geometry follows the shared-kernel stage.',
                'The semantic dependency on kernel stabilizes policy and lifecycle context before locality objects appear.',
            ),
        ),
        SubsystemManifest(
            name='judgments',
            package='jugeo.judgments',
            capabilities=(CapabilityFlag.JUDGMENTS,),
            dependencies=('geometry',),
            stage='shared-judgments',
            authority_boundary=(
                'Defines judgment syntax, contexts, sections, comparison maps, and explanation projections, '
                'but it does not promote local admissibility into certified truth without evidence federation.'
            ),
            scope_honesty=(
                'May project internal judgment state into lower-authority views, yet it must keep loss and scope '
                'visible rather than silently widening the settled region.'
            ),
            stable_exports=(
                'semantic contexts',
                'judgment terms',
                'sections',
                'comparison maps',
                'explanation projections',
            ),
            residual_exports=(
                'pack-specific judgment vocabularies remain external',
                'rich proof object encodings remain chapter-local',
            ),
            evidence_channels=('proof', 'solver', 'runtime', 'controlled-semantic'),
            public_surfaces=('schema exports', 'comparison summaries', 'public explanation inputs'),
            generation_notes=(
                'Structural hint: shared-judgments begins after geometry/descent has stabilized.',
                'This ordering mirrors the theory claim that judgments live over semantic coordinates.',
            ),
        ),
        SubsystemManifest(
            name='evidence',
            package='jugeo.evidence',
            capabilities=(CapabilityFlag.EVIDENCE, CapabilityFlag.COPILOT_PROPOSALS),
            dependencies=('judgments',),
            stage='shared-evidence',
            authority_boundary=(
                'Federates proof, solver, runtime, human, and controlled-semantic routes; proposal channels can '
                'suggest clauses or refinements, but they cannot silently settle them.'
            ),
            scope_honesty=(
                'Records channel provenance and residuality explicitly, so mixed support never masquerades as a '
                'fully verified global certificate.'
            ),
            stable_exports=(
                'evidence channels',
                'provenance traces',
                'trust profiles',
                'evidence manifests',
                'certificate ingredients',
            ),
            residual_exports=(
                'channel-specific challenge logic can still grow in later shared modules',
                'copilot proposal routing remains bounded and non-settling by design',
            ),
            evidence_channels=('proof', 'solver', 'runtime', 'controlled-semantic', 'human-review'),
            public_surfaces=('evidence manifests', 'trust summaries', 'certificate projections'),
            generation_notes=(
                'Structural hint: shared-evidence comes after the judgment layer in the dependency wave.',
                'The copilot proposal token is legitimate here because theory2 assigns proposal jurisdiction without trust escalation.',
            ),
        ),
        SubsystemManifest(
            name='packs',
            package='jugeo.packs',
            capabilities=(CapabilityFlag.PACKS,),
            dependencies=('evidence',),
            stage='shared-packs',
            authority_boundary=(
                'Owns domain-pack descriptors, bridges, and federation boundaries, but it does not universalize '
                'pack-local laws into package-wide truth without explicit transport.'
            ),
            scope_honesty=(
                'May name available domain vocabularies and translations, yet every pack stays local to its declared '
                'site region, bridge inventory, and trust floor.'
            ),
            stable_exports=(
                'pack catalogs',
                'pack descriptors',
                'bridge slots',
                'federation boundaries',
                'pack authority records',
            ),
            residual_exports=(
                'downstream application packs remain outside the shared manifest',
                'bridge theorem completion remains explicit work rather than implicit inheritance',
            ),
            evidence_channels=('proof', 'runtime', 'human-review'),
            public_surfaces=('pack inventory', 'bridge listings', 'federation boundaries'),
            generation_notes=(
                'Structural hint: shared-packs follows shared-evidence in the stage plan.',
                'Semantically, packs depend on evidence because transported laws and donors must retain provenance and trust metadata.',
            ),
        ),
        SubsystemManifest(
            name='solver',
            package='jugeo.solver',
            capabilities=(CapabilityFlag.SOLVER,),
            dependencies=('evidence',),
            stage='shared-solver',
            authority_boundary=(
                'Owns fragment routing, solver sessions, reconstruction, and countermodels, but solver discharge is '
                'credited only inside the declared fragment and timeout boundaries.'
            ),
            scope_honesty=(
                'May report discharge, fragments, and countermodels for its admitted theories, yet it must not claim '
                'semantic closure outside those fragment jurisdictions.'
            ),
            stable_exports=(
                'fragment registries',
                'solver routing',
                'solver sessions',
                'countermodels',
                'reconstruction helpers',
            ),
            residual_exports=(
                'unsupported theories stay residual rather than being flattened into solver confidence',
                'global certificates still require downstream evidence and runtime integration',
            ),
            evidence_channels=('solver', 'runtime'),
            public_surfaces=('solver health', 'fragment reporting', 'countermodel exports'),
            generation_notes=(
                'Structural hint: the shared plan branches packs and solver out of evidence-era foundations.',
                'This semantic routing layer remains parallel to packs in dependency spirit even though the manifest linearizes the package order.',
            ),
        ),
        SubsystemManifest(
            name='runtime',
            package='jugeo.runtime',
            capabilities=(CapabilityFlag.RUNTIME,),
            dependencies=('solver',),
            stage='shared-runtime',
            authority_boundary=(
                'Controls cache, replay, invalidation, and checkpointing boundaries, but runtime persistence does not '
                'make stale results current after invalidating events.'
            ),
            scope_honesty=(
                'Can reuse semantic state only within replay, support, and epoch limits; otherwise it must reopen or '
                'downgrade authority rather than pretending persistence implies truth.'
            ),
            stable_exports=(
                'checkpointing',
                'replay',
                'cache management',
                'memory surfaces',
                'invalidation mechanics',
            ),
            residual_exports=(
                'fine-grained epoch ledgers remain expandable',
                'distributed runtime witnesses remain outside the shared core',
            ),
            evidence_channels=('runtime', 'solver'),
            public_surfaces=('cache summaries', 'replay metadata', 'invalidation reports'),
            generation_notes=(
                'Structural hint: runtime follows solver in the shared generation wave.',
                'The semantic reason is theory2 manifest integrity: runtime owns epochs, replay, and stale-manifest conservativity.'
            ),
        ),
        SubsystemManifest(
            name='generation',
            package='jugeo.generation',
            capabilities=(CapabilityFlag.GENERATION,),
            dependencies=('runtime',),
            stage='shared-generation',
            authority_boundary=(
                'Coordinates goal selection, construction, backpressure, and treaty-aware generation, but it does not '
                'equate proposal success with certified settlement.'
            ),
            scope_honesty=(
                'May lower obstruction burden by proposing local sections or refinements, yet any emitted artifact must '
                'still pass through the evidence and runtime trust boundaries already declared.'
            ),
            stable_exports=(
                'construction orchestration',
                'goal records',
                'backpressure control',
                'integration helpers',
                'treaty-aware generation',
            ),
            residual_exports=(
                'chapter-specific generative heuristics remain downstream',
                'novel invariant discovery remains explicitly speculative until evidenced',
            ),
            evidence_channels=('controlled-semantic', 'runtime', 'human-review'),
            public_surfaces=('goal summaries', 'construction diagnostics', 'treaty projections'),
            generation_notes=(
                'Structural hint: shared-generation begins only after runtime mechanics exist.',
                'The theory-level reason is that generation acts on a live semantic state with replay and invalidation boundaries.',
            ),
        ),
        SubsystemManifest(
            name='orchestration',
            package='jugeo.orchestration',
            capabilities=(CapabilityFlag.ORCHESTRATION,),
            dependencies=('generation',),
            stage='shared-orchestration',
            authority_boundary=(
                'Owns controller state, frontier policies, budgets, and negotiation, but scheduling decisions do not by '
                'themselves certify semantic closure.'
            ),
            scope_honesty=(
                'Can prioritize repair and search frontiers according to expected reduction of obstruction burden, yet it '
                'must still report remaining residuals and trust limits explicitly.'
            ),
            stable_exports=(
                'controllers',
                'frontier management',
                'fleet and negotiation records',
                'budget surfaces',
                'semantic-control helpers',
            ),
            residual_exports=(
                'long-horizon multi-agent strategies remain tunable',
                'domain-specific frontier objectives remain local extensions',
            ),
            evidence_channels=('runtime', 'controlled-semantic', 'human-review'),
            public_surfaces=('frontier state', 'budget reports', 'routing diagnostics'),
            generation_notes=(
                'Structural hint: shared-orchestration follows shared-generation.',
                'This mirrors theory2: orchestration acts over changing descent state rather than replacing it.',
            ),
        ),
        SubsystemManifest(
            name='ideation',
            package='jugeo.ideation',
            capabilities=(CapabilityFlag.IDEATION,),
            dependencies=('orchestration',),
            stage='shared-ideation',
            authority_boundary=(
                'Owns novelty search, discovery federation, theorem ecologies, and scheduling for semantic futures, but '
                'ideation remains proposal-oriented until later evidence closes the obligations it emits.'
            ),
            scope_honesty=(
                'May search for new covers, regimes, invariants, or domain extensions, yet it cannot present speculative '
                'ideas as settled math or settled software semantics.'
            ),
            stable_exports=(
                'idea registries',
                'novelty search',
                'regime tracking',
                'research assistance hooks',
                'theory navigation aids',
            ),
            residual_exports=(
                'new domain mathematics stays explicit until transported back into the shared support regime',
                'future semantic scenarios remain bounded by their declared purpose and trust level',
            ),
            evidence_channels=('controlled-semantic', 'human-review', 'runtime'),
            public_surfaces=('idea summaries', 'regime schedules', 'research-assistance projections'),
            generation_notes=(
                'Structural hint: shared-ideation follows orchestration in the overall wave.',
                'The semantic rationale is theory2’s purpose-conditioned search over future semantic state.',
            ),
        ),
        SubsystemManifest(
            name='interfaces',
            package='jugeo.interfaces',
            capabilities=(CapabilityFlag.INTERFACES,),
            dependencies=('ideation',),
            stage='shared-interfaces',
            authority_boundary=(
                'Projects internal state into API, schema, serialization, diagnostics, and CLI surfaces, but it may not '
                'invent stronger closure than the manifest and evidence layers already support.'
            ),
            scope_honesty=(
                'Public outputs may compress detail, yet they must preserve support scope, residuality, and authority '
                'limits rather than widening what appears settled.'
            ),
            stable_exports=(
                'API sessions',
                'CLI projections',
                'serialization helpers',
                'diagnostics views',
                'schema exports',
            ),
            residual_exports=(
                'public example traces remain separately certifiable artifacts',
                'surface-specific vocabularies can refine later without changing the shared honesty boundary',
            ),
            evidence_channels=('runtime', 'human-review', 'solver'),
            public_surfaces=('api', 'cli', 'serialization', 'schema', 'diagnostics'),
            generation_notes=(
                'Structural hint: shared-interfaces closes the shared subsystem chain.',
                'This is theory2’s surface-manifest role: interfaces are public projections, not alternate sources of semantic authority.',
            ),
        ),
    )


def _validate_package_manifest(manifest: PackageManifest) -> None:
    seen: set[str] = set()
    authority_subsystems: list[str] = []
    capability_sources: dict[CapabilityFlag, list[str]] = {}
    for subsystem in manifest.subsystems:
        if subsystem.name in seen:
            _raise_manifest_error(
                code='manifest-duplicate-subsystem',
                summary='Subsystem names must be unique.',
                details={'subsystem': subsystem.name},
            )
        for dependency in subsystem.dependencies:
            if dependency not in seen:
                _raise_manifest_error(
                    code='manifest-dependency-order-violation',
                    summary='Subsystem dependencies must appear earlier in canonical order.',
                    details={'subsystem': subsystem.name, 'dependency': dependency},
                )
        if CapabilityFlag.AUTHORITY in subsystem.capabilities:
            authority_subsystems.append(subsystem.name)
        for capability in subsystem.capabilities:
            capability_sources.setdefault(capability, []).append(subsystem.name)
        seen.add(subsystem.name)

    if authority_subsystems != ['kernel']:
        _raise_manifest_error(
            code='manifest-authority-boundary-violation',
            summary='The shared package manifest reserves package authority to the kernel subsystem.',
            details={'authority_subsystems': authority_subsystems},
        )

    if capability_sources.get(CapabilityFlag.COPILOT_PROPOSALS) != ['evidence']:
        _raise_manifest_error(
            code='manifest-proposal-boundary-violation',
            summary='copilot proposal capability must remain confined to the evidence subsystem.',
            details={'sources': capability_sources.get(CapabilityFlag.COPILOT_PROPOSALS, [])},
        )


def _build_capability_surfaces(subsystems: tuple[SubsystemManifest, ...]) -> Mapping[str, ManifestCapability]:
    by_capability: dict[CapabilityFlag, list[SubsystemManifest]] = {}
    for subsystem in subsystems:
        for capability in subsystem.capabilities:
            by_capability.setdefault(capability, []).append(subsystem)

    surfaces: dict[str, ManifestCapability] = {}
    for capability in CapabilityFlag:
        exporters = by_capability.get(capability)
        if not exporters:
            continue
        surface_stage = exporters[0].stage
        surfaced_by = tuple(subsystem.name for subsystem in exporters)
        rationale = _compose_capability_rationale(capability, exporters)
        authority_boundary = ' | '.join(subsystem.authority_boundary for subsystem in exporters)
        honest_scope = ' | '.join(subsystem.scope_honesty for subsystem in exporters)
        surfaces[capability.value] = ManifestCapability(
            name=capability.value,
            enabled=True,
            stage=surface_stage,
            rationale=rationale,
            surfaced_by=surfaced_by,
            authority_boundary=authority_boundary,
            honest_scope=honest_scope,
        )
    return MappingProxyType(surfaces)


def _compose_capability_rationale(capability: CapabilityFlag, exporters: list[SubsystemManifest]) -> str:
    joined_names = ', '.join(subsystem.name for subsystem in exporters)
    if capability is CapabilityFlag.COPILOT_PROPOSALS:
        return (
            'Evidence federation exposes a bounded copilot proposal route for typed semantic suggestions; '
            f'the surface is exported by {joined_names} and remains non-settling.'
        )
    if capability is CapabilityFlag.AUTHORITY:
        return (
            'Kernel policy and delegation rules publish the shared authority ceiling; '
            f'the surface is exported by {joined_names} and does not certify downstream domain claims.'
        )
    if capability is CapabilityFlag.INTERFACES:
        return (
            'Interfaces project internal state onto public API, CLI, schema, and diagnostics surfaces while '
            f'preserving scope honesty; the surface is exported by {joined_names}.'
        )
    if capability is CapabilityFlag.GEOMETRY:
        return (
            'Geometry supplies the site, cover, support, and descent vocabulary that the rest of the shared core '
            f'uses to reason locally and globally; the surface is exported by {joined_names}.'
        )
    if capability is CapabilityFlag.JUDGMENTS:
        return (
            'Judgments carry context-sensitive clauses, sections, and comparison projections over semantic coordinates; '
            f'the surface is exported by {joined_names}.'
        )
    if capability is CapabilityFlag.EVIDENCE:
        return (
            'Evidence federation keeps proof, solver, runtime, human, and controlled-semantic routes distinct yet '
            f'traceable; the surface is exported by {joined_names}.'
        )
    if capability is CapabilityFlag.RUNTIME:
        return (
            'Runtime owns replay, cache, checkpoint, memory, and invalidation mechanics needed for stale-manifest '
            f'conservativity; the surface is exported by {joined_names}.'
        )
    if capability is CapabilityFlag.CONFIGURATION:
        return (
            'Configuration defaults and typed layering stay explicit rather than hidden in ad hoc dictionaries; '
            f'the surface is exported by {joined_names}.'
        )
    return (
        f'{capability.value} is a stable shared-core capability exported by {joined_names}; '
        'the manifest records it as a public surface because theory2 requires explicit subsystem manifests.'
    )


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{field_name} must be a string.')
    normalized = value.strip()
    if not normalized:
        raise ValueError(f'{field_name} must be non-empty.')
    return normalized


def _normalize_text_tuple(values: Iterable[object], *, field_name: str) -> tuple[str, ...]:
    if not _is_nonstring_iterable(values):
        raise TypeError(f'{field_name} must be an iterable of strings.')
    ordered: dict[str, None] = {}
    for value in values:
        ordered[_normalize_required_text(value, field_name=field_name)] = None
    return tuple(ordered)


def _normalize_capabilities(values: Iterable[CapabilityFlag | str]) -> tuple[CapabilityFlag, ...]:
    if not _is_nonstring_iterable(values):
        raise TypeError('capabilities must be an iterable of capability flags.')
    ordered: dict[CapabilityFlag, None] = {}
    for value in values:
        capability = value if isinstance(value, CapabilityFlag) else CapabilityFlag(_normalize_required_text(value, field_name='capability'))
        ordered[capability] = None
    if not ordered:
        raise ValueError('capabilities may not be empty.')
    return tuple(ordered)


def _normalize_subsystems(values: Iterable[SubsystemManifest]) -> tuple[SubsystemManifest, ...]:
    if not _is_nonstring_iterable(values):
        raise TypeError('subsystems must be an iterable of SubsystemManifest instances.')
    subsystems = tuple(values)
    if not all(isinstance(subsystem, SubsystemManifest) for subsystem in subsystems):
        raise TypeError('subsystems must contain only SubsystemManifest instances.')
    return subsystems


def _validate_subsystem_payload(payload: Mapping[str, Any], *, index: int) -> None:
    required = {
        'name',
        'package',
        'capabilities',
        'dependencies',
        'stage',
        'authority_boundary',
        'scope_honesty',
        'stable_exports',
        'residual_exports',
        'evidence_channels',
        'public_surfaces',
        'generation_notes',
    }
    missing = sorted(required.difference(payload))
    if missing:
        _raise_manifest_error(
            code='manifest-subsystem-missing-fields',
            summary='Subsystem payload is missing required fields.',
            details={'index': index, 'missing': missing},
        )
    for field_name in ('name', 'package', 'stage', 'authority_boundary', 'scope_honesty'):
        try:
            _normalize_required_text(payload[field_name], field_name=field_name)
        except (TypeError, ValueError) as exc:
            _raise_manifest_error(
                code='manifest-invalid-subsystem-field',
                summary='Subsystem payload contains an invalid text field.',
                details={'index': index, 'field': field_name, 'error': str(exc)},
            )
    for field_name in ('capabilities', 'dependencies', 'stable_exports', 'residual_exports', 'evidence_channels', 'public_surfaces', 'generation_notes'):
        if not _is_nonstring_iterable(payload[field_name]):
            _raise_manifest_error(
                code='manifest-invalid-subsystem-sequence',
                summary='Subsystem payload contains an invalid sequence field.',
                details={'index': index, 'field': field_name, 'type': type(payload[field_name]).__name__},
            )


def _validate_capability_payload(capability_name: object, payload: Any) -> None:
    try:
        _normalize_required_text(capability_name, field_name='capability name')
    except (TypeError, ValueError) as exc:
        _raise_manifest_error(
            code='manifest-invalid-capability-name',
            summary='Manifest capability names must be non-empty strings.',
            details={'error': str(exc)},
        )
    if not isinstance(payload, Mapping):
        _raise_manifest_error(
            code='manifest-invalid-capability-entry',
            summary='Each capability entry must be a mapping.',
            details={'capability': capability_name, 'type': type(payload).__name__},
        )
    required = {'enabled', 'stage', 'rationale', 'surfaced_by', 'authority_boundary', 'honest_scope'}
    missing = sorted(required.difference(payload))
    if missing:
        _raise_manifest_error(
            code='manifest-capability-missing-fields',
            summary='Capability payload is missing required fields.',
            details={'capability': capability_name, 'missing': missing},
        )
    if not isinstance(payload['enabled'], bool):
        _raise_manifest_error(
            code='manifest-capability-invalid-enabled',
            summary='Capability enabled flags must be boolean.',
            details={'capability': capability_name, 'type': type(payload['enabled']).__name__},
        )
    for field_name in ('stage', 'rationale', 'authority_boundary', 'honest_scope'):
        try:
            _normalize_required_text(payload[field_name], field_name=f'capability {field_name}')
        except (TypeError, ValueError) as exc:
            _raise_manifest_error(
                code='manifest-capability-invalid-field',
                summary='Capability payload contains an invalid text field.',
                details={'capability': capability_name, 'field': field_name, 'error': str(exc)},
            )
    if not _is_nonstring_iterable(payload['surfaced_by']):
        _raise_manifest_error(
            code='manifest-capability-invalid-surfaced-by',
            summary='Capability surfaced_by must be an iterable of subsystem names.',
            details={'capability': capability_name, 'type': type(payload['surfaced_by']).__name__},
        )


def _is_nonstring_iterable(value: object) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray))


def _raise_manifest_error(*, code: str, summary: str, details: Mapping[str, Any]) -> None:
    raise_with_scope(
        code,
        message=summary,
        scope=FailureScope.ROOT,
        trust_boundary='package-manifest',
        provenance=details,
    )


__all__ = [
    'CapabilityFlag',
    'ManifestCapability',
    'SubsystemManifest',
    'PackageManifest',
    'MANIFEST_SPEC_PROVENANCE',
    'THEOREM_TARGETS',
    'build_package_manifest',
    'validate_manifest_shape',
    'enumerate_subsystems',
    # Unified judgment-geometric manifest helpers
    'manifest_from_site',
    'manifest_evidence',
    'manifest_maturity',
]


# ---------------------------------------------------------------------------
# Unified judgment-geometric manifest helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import GeometricSite as _GeometricSite  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _GeometricSite = None

try:
    from jugeo.evidence.manifests import EvidenceManifest as _EvidenceManifest  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _EvidenceManifest = None

try:
    from jugeo.maturity import maturity_score as _maturity_score  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _maturity_score = None


def manifest_from_site(site: Any) -> dict[str, Any]:
    """Derive a package manifest fragment from a geometric site.

    Uses ``jugeo.geometry.site`` to extract subsystem topology and
    dependency edges that feed into the canonical package manifest.
    """
    if _GeometricSite is None:
        return {"error": "jugeo.geometry.site not available"}
    try:
        return _GeometricSite.to_manifest(site)
    except Exception as exc:
        return {"error": str(exc)}


def manifest_evidence(manifest: Any) -> dict[str, Any]:
    """Attach evidence metadata to an existing manifest.

    Uses ``jugeo.evidence.manifests`` to annotate the manifest with
    evidence provenance, channel assignments, and trust accounting.
    """
    if _EvidenceManifest is None:
        return {"error": "jugeo.evidence.manifests not available"}
    try:
        return _EvidenceManifest.annotate(manifest)
    except Exception as exc:
        return {"error": str(exc)}


def manifest_maturity(manifest: Any) -> dict[str, Any]:
    """Compute the maturity score for a manifest.

    Uses ``jugeo.maturity`` to evaluate the cyclic-picture maturity
    level of the subsystems described in *manifest*.
    """
    if _maturity_score is None:
        return {"error": "jugeo.maturity not available"}
    try:
        score = _maturity_score(manifest)
        return {"maturity_score": score}
    except Exception as exc:
        return {"error": str(exc)}
