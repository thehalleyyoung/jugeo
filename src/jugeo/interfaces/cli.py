"""copilot-visible CLI entry points for JuGeo public semantic reporting.

This module deliberately treats the command-line surface as a semantic object
rather than as a thin parser. The design follows the themes made explicit in
``preliminaries/theory2.tex``:

* public honesty: exported text may summarize internal state, but it may not
  silently strengthen support or hide residuality;
* scope-aware reporting: outputs can name the scope coordinate that governs a
  claim, instead of pretending every observation is context-free;
* residual visibility: unresolved work is first-class and stable enough to be
  cited from text or JSON projections; and
* operational control surfaces: the CLI exposes a small, explicit controller
  state with frontier and budget information instead of hiding all operational
  choices behind opaque prose.

The implementation is intentionally self-contained so that humans and language
models can read the public projection logic directly. It does not claim solver
proofs that the runtime does not possess. Instead, it projects what is really
available from the current package manifest and API session, while making the
remaining gaps visible as residual obligations.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from enum import Enum
import io
import json
import textwrap
from typing import Any, Iterable, Mapping, Sequence

from jugeo.interfaces.api import JuGeoAPI
from jugeo.package_manifest import PackageManifest, build_package_manifest

__all__ = [
    'CLIApplication',
    'CLIContext',
    'ControlSurface',
    'FrontierNode',
    'PublicClaim',
    'ResidualObligation',
    'ScopeCoordinate',
    'main',
    'register_commands',
]


class ParserExit(RuntimeError):
    """Internal exception used to make argparse testable and non-interactive."""

    def __init__(self, status: int = 0) -> None:
        super().__init__(status)
        self.status = status


class HonestArgumentParser(argparse.ArgumentParser):
    """Argument parser that writes to a provided text stream and never exits."""

    def __init__(self, *args: Any, output: io.StringIO, **kwargs: Any) -> None:
        self._output = output
        super().__init__(*args, **kwargs)

    def _print_message(self, message: str | None, file: io.TextIOBase | None = None) -> None:
        if not message:
            return
        target = self._output if file is None else file
        target.write(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            self._print_message(message)
        raise ParserExit(status)

    def error(self, message: str) -> None:
        self.print_usage(self._output)
        self.exit(2, f'{self.prog}: error: {message}\n')


class OutputFormat(str, Enum):
    TEXT = 'text'
    JSON = 'json'


class TrustLabel(str, Enum):
    SOLVER_PROVEN = 'solver-proven'
    RUNTIME_BACKED = 'runtime-backed'
    CONTROLLED_JUDGMENT = 'controlled-semantic-judgment'
    RESIDUAL = 'residual'


class ResidualKind(str, Enum):
    STRUCTURAL = 'structural'
    SEMANTIC = 'semantic'
    RELATIONAL = 'relational'
    ORCHESTRATION = 'orchestration'
    DOMAIN_PACK = 'domain-pack-specific'


class UsageKind(str, Enum):
    READ = 'read'
    WRITE = 'write'
    CLOSURE_CAPTURE = 'closure-capture'
    IMPORT_PROJECTION = 'import-projection'
    REFLECTIVE_ACCESS = 'reflective-access'


class EvidenceRoute(str, Enum):
    RUNTIME = 'runtime'
    SOLVER = 'solver'
    CONTROLLED_LLM = 'controlled-llm'
    HUMAN = 'human-review'


@dataclass(frozen=True, slots=True)
class ScopeCoordinate:
    """Theory-shaped scope address for public reporting.

    The coordinate mirrors the tuple described in theory2.tex: local summary,
    global summary, captured-cell family, owning module, active epoch, and
    usage kind. The values are intentionally summarized strings rather than raw
    runtime objects because the CLI is a public projection, not a debug dump.
    """

    local_summary: str
    global_summary: str
    cell_summary: str
    module_coordinate: str
    epoch: str
    usage_kind: UsageKind

    def with_usage(self, usage_kind: UsageKind) -> 'ScopeCoordinate':
        return ScopeCoordinate(
            local_summary=self.local_summary,
            global_summary=self.global_summary,
            cell_summary=self.cell_summary,
            module_coordinate=self.module_coordinate,
            epoch=self.epoch,
            usage_kind=usage_kind,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            'local_summary': self.local_summary,
            'global_summary': self.global_summary,
            'cell_summary': self.cell_summary,
            'module_coordinate': self.module_coordinate,
            'epoch': self.epoch,
            'usage_kind': self.usage_kind.value,
        }

    def render_text(self) -> str:
        return (
            'scope('
            f'Γ_loc={self.local_summary}; '
            f'Γ_glob={self.global_summary}; '
            f'Γ_cell={self.cell_summary}; '
            f'module={self.module_coordinate}; '
            f'epoch={self.epoch}; '
            f'usage={self.usage_kind.value}'
            ')'
        )


@dataclass(frozen=True, slots=True)
class ResidualObligation:
    """Stable public record for unresolved obligations that still matter."""

    residual_id: str
    title: str
    kind: ResidualKind
    location: str
    discharge_expectation: str
    depends_on: tuple[str, ...]
    public_effect: str
    evidence_route: EvidenceRoute

    def to_dict(self) -> dict[str, Any]:
        return {
            'residual_id': self.residual_id,
            'title': self.title,
            'kind': self.kind.value,
            'location': self.location,
            'discharge_expectation': self.discharge_expectation,
            'depends_on': list(self.depends_on),
            'public_effect': self.public_effect,
            'evidence_route': self.evidence_route.value,
        }

    def render_text(self) -> str:
        dependency_text = ', '.join(self.depends_on) if self.depends_on else 'none'
        return (
            f'- {self.residual_id} [{self.kind.value}] {self.title}\n'
            f'  location: {self.location}\n'
            f'  discharge: {self.discharge_expectation}\n'
            f'  depends_on: {dependency_text}\n'
            f'  public_effect: {self.public_effect}\n'
            f'  evidence_route: {self.evidence_route.value}'
        )


@dataclass(frozen=True, slots=True)
class PublicClaim:
    """Publicly exportable claim with explicit support and residual linkage."""

    claim_id: str
    clause_id: str
    title: str
    summary: str
    trust: TrustLabel
    evidence_route: EvidenceRoute
    scope: ScopeCoordinate
    residual_ids: tuple[str, ...] = ()
    donor_provenance: str | None = None
    citations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            'claim_id': self.claim_id,
            'clause_id': self.clause_id,
            'title': self.title,
            'summary': self.summary,
            'trust': self.trust.value,
            'evidence_route': self.evidence_route.value,
            'scope': self.scope.to_dict(),
            'residual_ids': list(self.residual_ids),
            'donor_provenance': self.donor_provenance,
            'citations': list(self.citations),
        }

    def render_text(self) -> str:
        residual_text = ', '.join(self.residual_ids) if self.residual_ids else 'none'
        donor_text = self.donor_provenance or 'local-runtime'
        citation_text = ', '.join(self.citations) if self.citations else 'none'
        return (
            f'- {self.claim_id} ({self.clause_id}) {self.title}\n'
            f'  trust: {self.trust.value}\n'
            f'  evidence_route: {self.evidence_route.value}\n'
            f'  summary: {self.summary}\n'
            f'  scope: {self.scope.render_text()}\n'
            f'  residuals: {residual_text}\n'
            f'  donor_provenance: {donor_text}\n'
            f'  citations: {citation_text}'
        )


@dataclass(frozen=True, slots=True)
class RouteBudget:
    """Small public budget slice for one evidence route."""

    route: EvidenceRoute
    capacity: int
    reserved: int
    note: str

    @property
    def available(self) -> int:
        return max(self.capacity - self.reserved, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            'route': self.route.value,
            'capacity': self.capacity,
            'reserved': self.reserved,
            'available': self.available,
            'note': self.note,
        }

    def render_text(self) -> str:
        return (
            f'- {self.route.value}: capacity={self.capacity}, '
            f'reserved={self.reserved}, available={self.available}; {self.note}'
        )


@dataclass(frozen=True, slots=True)
class FrontierNode:
    """Candidate future state exposed through the control surface."""

    node_id: str
    title: str
    support_region: str
    expected_route: EvidenceRoute
    typed: bool
    scoped: bool
    replay_named: bool
    failure_schema: str | None
    closure_gain: float
    stability_gain: float
    bridge_gain: float
    optionality_gain: float
    cost: float
    overclaim_risk: float

    @property
    def admissible(self) -> bool:
        return self.typed and self.scoped and self.replay_named and bool(self.failure_schema)

    def leverage_score(self, phase: str) -> float:
        weights = {
            'alignment': (1.0, 1.0, 0.8, 0.4, 0.9, 1.2),
            'stabilization': (0.8, 1.4, 0.7, 0.3, 0.8, 1.1),
            'growth': (1.1, 0.9, 1.2, 1.0, 0.7, 1.0),
            'public-reporting': (0.9, 1.2, 0.6, 0.5, 0.8, 1.4),
        }
        w1, w2, w3, w4, w5, w6 = weights.get(phase, weights['public-reporting'])
        return (
            (w1 * self.closure_gain)
            + (w2 * self.stability_gain)
            + (w3 * self.bridge_gain)
            + (w4 * self.optionality_gain)
            - (w5 * self.cost)
            - (w6 * self.overclaim_risk)
        )

    def to_dict(self, phase: str) -> dict[str, Any]:
        return {
            'node_id': self.node_id,
            'title': self.title,
            'support_region': self.support_region,
            'expected_route': self.expected_route.value,
            'typed': self.typed,
            'scoped': self.scoped,
            'replay_named': self.replay_named,
            'failure_schema': self.failure_schema,
            'admissible': self.admissible,
            'semantic_leverage': round(self.leverage_score(phase), 3),
            'components': {
                'closure_gain': self.closure_gain,
                'stability_gain': self.stability_gain,
                'bridge_gain': self.bridge_gain,
                'optionality_gain': self.optionality_gain,
                'cost': self.cost,
                'overclaim_risk': self.overclaim_risk,
            },
        }

    def render_text(self, phase: str) -> str:
        failure_text = self.failure_schema or 'missing'
        return (
            f'- {self.node_id}: {self.title}\n'
            f'  route: {self.expected_route.value}\n'
            f'  support_region: {self.support_region}\n'
            f'  admissible: {self.admissible}\n'
            f'  semantic_leverage[{phase}]: {self.leverage_score(phase):.3f}\n'
            f'  typed={self.typed}, scoped={self.scoped}, replay_named={self.replay_named}\n'
            f'  failure_schema: {failure_text}'
        )


@dataclass(frozen=True, slots=True)
class ControlSurface:
    """Public control state derived from theory-shaped orchestration fields."""

    generation_state: str
    phase: str
    budgets: tuple[RouteBudget, ...]
    frontier: tuple[FrontierNode, ...]
    replay_note: str

    def frontier_for_display(self, *, only_admissible: bool = False) -> tuple[FrontierNode, ...]:
        nodes = self.frontier
        if only_admissible:
            nodes = tuple(node for node in nodes if node.admissible)
        return tuple(sorted(nodes, key=lambda node: node.leverage_score(self.phase), reverse=True))

    def to_dict(self, *, only_admissible: bool = False) -> dict[str, Any]:
        frontier = self.frontier_for_display(only_admissible=only_admissible)
        return {
            'generation_state': self.generation_state,
            'phase': self.phase,
            'budgets': [budget.to_dict() for budget in self.budgets],
            'frontier': [node.to_dict(self.phase) for node in frontier],
            'replay_note': self.replay_note,
        }

    def render_text(self, *, only_admissible: bool = False) -> str:
        lines = [
            f'generation_state: {self.generation_state}',
            f'phase: {self.phase}',
            'budgets:',
            *[budget.render_text() for budget in self.budgets],
            'frontier:',
            *[node.render_text(self.phase) for node in self.frontier_for_display(only_admissible=only_admissible)],
            f'replay_note: {self.replay_note}',
        ]
        return '\n'.join(lines)


@dataclass(frozen=True, slots=True)
class SurfaceSnapshot:
    """Combined public surface snapshot used by several CLI commands."""

    package_name: str
    manifest: PackageManifest
    scope: ScopeCoordinate
    claims: tuple[PublicClaim, ...]
    residuals: tuple[ResidualObligation, ...]
    control: ControlSurface

    def claim_map(self) -> dict[str, PublicClaim]:
        return {claim.claim_id: claim for claim in self.claims}

    def residual_map(self) -> dict[str, ResidualObligation]:
        return {residual.residual_id: residual for residual in self.residuals}

    def to_dict(
        self,
        *,
        include_claims: bool = True,
        include_residuals: bool = True,
        include_control: bool = True,
        only_admissible: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'package_name': self.package_name,
            'manifest': self.manifest.to_dict(),
            'scope': self.scope.to_dict(),
            'public_honesty_rule': 'summaries may forget detail but may not strengthen support',
        }
        if include_claims:
            payload['claims'] = [claim.to_dict() for claim in self.claims]
        if include_residuals:
            payload['residuals'] = [residual.to_dict() for residual in self.residuals]
        if include_control:
            payload['control'] = self.control.to_dict(only_admissible=only_admissible)
        return payload

    def render_text(
        self,
        *,
        include_claims: bool = True,
        include_residuals: bool = True,
        include_control: bool = True,
        only_admissible: bool = False,
    ) -> str:
        lines = [
            f'package_name: {self.package_name}',
            f'package_version: {self.manifest.version}',
            f'subsystem_order: {", ".join(self.manifest.subsystem_order)}',
            'public_honesty: summaries may forget detail but may not strengthen support',
            f'scope: {self.scope.render_text()}',
        ]
        if include_claims:
            lines.append('claims:')
            lines.extend(claim.render_text() for claim in self.claims)
        if include_residuals:
            lines.append('residuals:')
            lines.extend(residual.render_text() for residual in self.residuals)
        if include_control:
            lines.append('control:')
            lines.append(self.control.render_text(only_admissible=only_admissible))
        return '\n'.join(lines)


@dataclass(slots=True)
class CLIContext:
    api: JuGeoAPI = field(default_factory=JuGeoAPI.create)
    output: io.StringIO = field(default_factory=io.StringIO)
    structured_payload: Any | None = None

    def reset(self) -> None:
        self.output.seek(0)
        self.output.truncate(0)
        self.structured_payload = None

    def write_line(self, line: str = '') -> None:
        self.output.write(line)
        self.output.write('\n')


@dataclass(slots=True)
class CLIApplication:
    """Theory-aligned CLI that projects public state without overclaiming."""

    context: CLIContext = field(default_factory=CLIContext)

    def judgment_schema(self) -> dict[str, Any]:
        return {
            'title': 'JuGeo Judgment Export',
            'type': 'object',
            'description': (
                'Stable projection schema for JuGeo judgment and section exports. '
                'Public projections remain loss-declaring, scope-honest, and residual-visible.'
            ),
            'one_of': ['judgment-export', 'section-export'],
            'required': (
                'projection',
                'coordinate',
                'proposition',
                'status',
                'residual_count',
                'obstruction_count',
                'trust_summary',
                'provenance_length',
                'loss_declared',
                'evidence_refs',
                'residuals',
                'obstructions',
                'provenance',
                'positive_clauses',
                'qualified_clauses',
                'public_evidence',
                'fragility',
                'artifact_summary',
                'clauses',
                'residual_visibility',
                'provenance_visible',
                'certificate_surface',
            ),
            'section_only': (
                'scope',
                'patch',
                'support_labels',
                'support_provenance',
                'section_provenance',
                'section_provenance_length',
                'scope_size',
            ),
            'notes': (
                'Section exports extend judgment exports with explicit scope and support provenance.',
                'Public and diagnostic projections declare loss; internal projections remain least lossy.',
            ),
        }

    def build_parser(self) -> HonestArgumentParser:
        parser = HonestArgumentParser(
            prog='jugeo',
            output=self.context.output,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description='JuGeo public semantic CLI with honesty, scope, residual, and control reporting.',
            epilog=textwrap.dedent(
                """\
                Public surface policy:
                  * report trust labels rather than implying stronger support;
                  * keep residual obligations visible with stable identifiers;
                  * expose scope coordinates when a claim depends on module state;
                  * surface operational control choices as replayable, typed futures.

                Example invocations:
                  jugeo manifest --format json --show-capabilities
                  jugeo health --show-scope
                  jugeo residuals --kind orchestration --include-dependencies
                  jugeo control --only-admissible --phase public-reporting
                  jugeo report --format json --include-control
                  jugeo explain claim.health.session-open
                """
            ),
        )
        register_commands(parser)
        return parser

    def run(self, argv: Sequence[str] | None = None) -> int:
        self.context.reset()
        parser = self.build_parser()
        try:
            args = parser.parse_args(argv)
        except ParserExit as exc:
            return exc.status

        command = getattr(args, 'command', None)
        if not command:
            parser.print_help(self.context.output)
            return 0

        handler = getattr(self, f'_handle_{command.replace("-", "_")}', None)
        if handler is None:
            parser.print_help(self.context.output)
            return 2
        return handler(args)

    def _handle_manifest(self, args: argparse.Namespace) -> int:
        snapshot = self._build_snapshot(command='manifest', phase=args.phase)
        if args.format == OutputFormat.JSON.value:
            payload = {
                'command': 'manifest',
                'package_name': snapshot.package_name,
                'manifest': snapshot.manifest.to_dict(),
                'claims': [claim.to_dict() for claim in snapshot.claims if claim.claim_id == 'claim.manifest.order'],
            }
            if args.show_capabilities:
                payload['capabilities'] = snapshot.manifest.to_dict()['capabilities']
            if args.include_residuals:
                payload['residuals'] = [residual.to_dict() for residual in snapshot.residuals]
            self._write_json(payload)
            return 0

        self.context.write_line(f'package_name: {snapshot.package_name}')
        self.context.write_line(f'subsystem_order: {", ".join(snapshot.manifest.subsystem_order)}')
        self.context.write_line('trust: runtime-backed')
        self.context.write_line('honesty_note: manifest output names runtime support and does not claim solver proof')
        if args.show_capabilities:
            self.context.write_line('capabilities:')
            for name, flag in snapshot.manifest.capabilities.items():
                self.context.write_line(
                    f'- {name}: enabled={flag.enabled}, stage={flag.stage}, rationale={flag.rationale}'
                )
        if args.include_residuals:
            self.context.write_line('residuals:')
            for residual in snapshot.residuals:
                self.context.write_line(residual.render_text())
        return 0

    def _handle_health(self, args: argparse.Namespace) -> int:
        snapshot = self._build_snapshot(command='health', phase=args.phase)
        claim = snapshot.claim_map()['claim.health.session-open']
        if args.format == OutputFormat.JSON.value:
            payload = {
                'command': 'health',
                'package_name': snapshot.package_name,
                'status': 'ready',
                'claim': claim.to_dict(),
            }
            if args.show_scope:
                payload['scope'] = claim.scope.to_dict()
            self._write_json(payload)
            return 0

        self.context.write_line(f'package_name: {snapshot.package_name}')
        self.context.write_line('status: ready')
        self.context.write_line(f'trust: {claim.trust.value}')
        self.context.write_line(f'evidence_route: {claim.evidence_route.value}')
        self.context.write_line(f'honesty_note: {claim.summary}')
        if args.show_scope:
            self.context.write_line(f'scope: {claim.scope.render_text()}')
        return 0

    def _handle_residuals(self, args: argparse.Namespace) -> int:
        snapshot = self._build_snapshot(command='residuals', phase=args.phase)
        residuals = self._filtered_residuals(snapshot.residuals, kind=args.kind)
        if args.format == OutputFormat.JSON.value:
            payload = {
                'command': 'residuals',
                'count': len(residuals),
                'residuals': [residual.to_dict() for residual in residuals],
            }
            self._write_json(payload)
            return 0

        self.context.write_line(f'residual_count: {len(residuals)}')
        self.context.write_line('public_honesty: residual obligations remain visible until discharged')
        for residual in residuals:
            self.context.write_line(residual.render_text())
            if not args.include_dependencies:
                continue
            dependency_text = ', '.join(residual.depends_on) if residual.depends_on else 'none'
            self.context.write_line(f'  dependency_summary: {dependency_text}')
        return 0

    def _handle_control(self, args: argparse.Namespace) -> int:
        snapshot = self._build_snapshot(command='control', phase=args.phase)
        if args.format == OutputFormat.JSON.value:
            payload = {
                'command': 'control',
                'control': snapshot.control.to_dict(only_admissible=args.only_admissible),
            }
            self._write_json(payload)
            return 0

        self.context.write_line(snapshot.control.render_text(only_admissible=args.only_admissible))
        return 0

    def _handle_report(self, args: argparse.Namespace) -> int:
        snapshot = self._build_snapshot(command='report', phase=args.phase)
        if args.format == OutputFormat.JSON.value:
            payload = {
                'command': 'report',
                'report': snapshot.to_dict(
                    include_claims=True,
                    include_residuals=args.include_residuals,
                    include_control=args.include_control,
                    only_admissible=args.only_admissible,
                ),
            }
            self._write_json(payload)
            return 0

        rendered = snapshot.render_text(
            include_claims=True,
            include_residuals=args.include_residuals,
            include_control=args.include_control,
            only_admissible=args.only_admissible,
        )
        self.context.write_line(rendered)
        return 0

    def _handle_explain(self, args: argparse.Namespace) -> int:
        snapshot = self._build_snapshot(command='explain', phase=args.phase)
        claim = snapshot.claim_map().get(args.claim_id)
        if claim is None:
            if args.format == OutputFormat.JSON.value:
                self._write_json({'command': 'explain', 'error': 'unknown-claim', 'available_claims': sorted(snapshot.claim_map())})
            else:
                available = ', '.join(sorted(snapshot.claim_map()))
                self.context.write_line(f'error: unknown-claim {args.claim_id}')
                self.context.write_line(f'available_claims: {available}')
            return 1

        cited_residuals = [snapshot.residual_map()[residual_id].to_dict() for residual_id in claim.residual_ids]
        if args.format == OutputFormat.JSON.value:
            self._write_json(
                {
                    'command': 'explain',
                    'claim': claim.to_dict(),
                    'cited_residuals': cited_residuals,
                    'public_honesty_rule': 'the explanation may summarize but does not strengthen support',
                }
            )
            return 0

        self.context.write_line(claim.render_text())
        self.context.write_line('public_honesty_rule: the explanation may summarize but does not strengthen support')
        if cited_residuals:
            self.context.write_line('cited_residuals:')
            for residual in self._filtered_residuals(snapshot.residuals, residual_ids=claim.residual_ids):
                self.context.write_line(residual.render_text())
        return 0

    def _handle_judgment_schema(self, args: argparse.Namespace) -> int:
        payload = self.judgment_schema()
        self.context.structured_payload = payload
        if args.format == OutputFormat.JSON.value:
            self._write_json(payload)
            return 0

        self.context.write_line(payload['title'])
        self.context.write_line(f"type: {payload['type']}")
        self.context.write_line(f"one_of: {', '.join(payload['one_of'])}")
        self.context.write_line(f"required: {', '.join(payload['required'])}")
        self.context.write_line(f"section_only: {', '.join(payload['section_only'])}")
        return 0

    def _build_snapshot(self, *, command: str, phase: str) -> SurfaceSnapshot:
        manifest = build_package_manifest()
        session = self.context.api.open_session()
        scope = self._default_scope(command)
        residuals = self._build_residuals(manifest)
        claims = self._build_claims(manifest=manifest, session_package_name=session.manifest.package_name, scope=scope)
        control = self._build_control_surface(manifest=manifest, residuals=residuals, phase=phase)
        return SurfaceSnapshot(
            package_name=session.manifest.package_name,
            manifest=manifest,
            scope=scope,
            claims=claims,
            residuals=residuals,
            control=control,
        )

    def _default_scope(self, command: str) -> ScopeCoordinate:
        usage_by_command = {
            'manifest': UsageKind.READ,
            'health': UsageKind.READ,
            'residuals': UsageKind.REFLECTIVE_ACCESS,
            'control': UsageKind.REFLECTIVE_ACCESS,
            'report': UsageKind.IMPORT_PROJECTION,
            'explain': UsageKind.REFLECTIVE_ACCESS,
        }
        return ScopeCoordinate(
            local_summary=f'command={command}; argv-scope=public-cli',
            global_summary='manifest=build_package_manifest; api=JuGeoAPI.open_session',
            cell_summary='no captured cells exported on the public surface',
            module_coordinate='jugeo.interfaces.cli',
            epoch='runtime-epoch:current-process',
            usage_kind=usage_by_command.get(command, UsageKind.READ),
        )

    def _build_residuals(self, manifest: PackageManifest) -> tuple[ResidualObligation, ...]:
        joined_order = ', '.join(manifest.subsystem_order)
        return (
            ResidualObligation(
                residual_id='residual.public.example-traces',
                title='Replayable public examples remain to be certified',
                kind=ResidualKind.STRUCTURAL,
                location='cli:examples',
                discharge_expectation='attach behavior samples whose traces cite current clause IDs and manifests',
                depends_on=(),
                public_effect='CLI help can describe commands, but example traces are intentionally not presented as certified behaviors yet.',
                evidence_route=EvidenceRoute.RUNTIME,
            ),
            ResidualObligation(
                residual_id='residual.scope.epoch-reopening',
                title='Module epoch reopening is described but not live-tracked',
                kind=ResidualKind.SEMANTIC,
                location='cli:scope',
                discharge_expectation='connect scope records to a runtime module epoch ledger so rebinding can reopen exactly affected judgments',
                depends_on=('residual.public.example-traces',),
                public_effect='Scope reports are honest summaries, not a proof that all epoch-sensitive dependencies are replayed.',
                evidence_route=EvidenceRoute.RUNTIME,
            ),
            ResidualObligation(
                residual_id='residual.migration.donor-provenance',
                title='Donor provenance is narrative-only for theory-driven imports',
                kind=ResidualKind.RELATIONAL,
                location='cli:migration',
                discharge_expectation='attach machine-readable migration treaties for imported semantics and public compatibility claims',
                depends_on=('residual.scope.epoch-reopening',),
                public_effect='The CLI cites theory2.tex as design provenance, but it does not claim a complete donor treaty for every public clause.',
                evidence_route=EvidenceRoute.HUMAN,
            ),
            ResidualObligation(
                residual_id='residual.control.route-certificates',
                title='Control routes need stronger witness schemas',
                kind=ResidualKind.ORCHESTRATION,
                location='cli:control',
                discharge_expectation='tie each frontier node to replayable success and failure schemas across runtime, solver, llm, and human routes',
                depends_on=('residual.scope.epoch-reopening', 'residual.migration.donor-provenance'),
                public_effect='Control surfaces expose intended routes, but some routes are still planning artifacts instead of certified executions.',
                evidence_route=EvidenceRoute.CONTROLLED_LLM,
            ),
            ResidualObligation(
                residual_id='residual.domainpack.vocabulary-bridges',
                title='Domain-pack translation hooks are not yet materialized on the CLI',
                kind=ResidualKind.DOMAIN_PACK,
                location='cli:domain-packs',
                discharge_expectation='publish vocabulary bridge inventories for subsystem families such as ' + joined_order,
                depends_on=('residual.migration.donor-provenance',),
                public_effect='The public surface names subsystem families, but it does not yet translate them through domain-pack-specific vocabularies.',
                evidence_route=EvidenceRoute.HUMAN,
            ),
        )

    def _build_claims(
        self,
        *,
        manifest: PackageManifest,
        session_package_name: str,
        scope: ScopeCoordinate,
    ) -> tuple[PublicClaim, ...]:
        subsystem_count = len(manifest.subsystem_order)
        return (
            PublicClaim(
                claim_id='claim.manifest.order',
                clause_id='cli.manifest.subsystem-order',
                title='Manifest order is projected from the current package manifest',
                summary=(
                    f'Runtime manifest inspection reports {subsystem_count} subsystems in their declared order '
                    'without claiming solver proof or cross-version permanence.'
                ),
                trust=TrustLabel.RUNTIME_BACKED,
                evidence_route=EvidenceRoute.RUNTIME,
                scope=scope.with_usage(UsageKind.READ),
                citations=('theory2.tex:531-542', 'theory2.tex:536-542'),
            ),
            PublicClaim(
                claim_id='claim.health.session-open',
                clause_id='cli.health.session-open',
                title='API session opens for the shared manifest package',
                summary=(
                    f'The CLI opened a JuGeo API session and observed package_name={session_package_name}; '
                    'the claim is runtime-backed for the current environment only.'
                ),
                trust=TrustLabel.RUNTIME_BACKED,
                evidence_route=EvidenceRoute.RUNTIME,
                scope=scope.with_usage(UsageKind.IMPORT_PROJECTION),
                citations=('theory2.tex:532-542', 'theory2.tex:587-603'),
            ),
            PublicClaim(
                claim_id='claim.surface.honesty-projector',
                clause_id='cli.surface.honesty-projector',
                title='Public reporting exposes trust labels, scope, and residual identifiers',
                summary=(
                    'The CLI intentionally reports trust labels, scope coordinates, and residual IDs so the public story '
                    'does not silently strengthen support relative to internal state.'
                ),
                trust=TrustLabel.CONTROLLED_JUDGMENT,
                evidence_route=EvidenceRoute.RUNTIME,
                scope=scope.with_usage(UsageKind.REFLECTIVE_ACCESS),
                residual_ids=(
                    'residual.public.example-traces',
                    'residual.scope.epoch-reopening',
                ),
                donor_provenance='design semantics projected from preliminaries/theory2.tex',
                citations=('theory2.tex:520-542',),
            ),
            PublicClaim(
                claim_id='claim.control.frontier-surface',
                clause_id='cli.control.frontier-surface',
                title='Operational control state is exposed as a typed frontier rather than an opaque queue',
                summary=(
                    'The CLI publishes admissible futures, route budgets, and phase-aware leverage scores to keep '
                    'operational control legible, but some routes remain residual planning artifacts.'
                ),
                trust=TrustLabel.CONTROLLED_JUDGMENT,
                evidence_route=EvidenceRoute.CONTROLLED_LLM,
                scope=scope.with_usage(UsageKind.REFLECTIVE_ACCESS),
                residual_ids=(
                    'residual.control.route-certificates',
                    'residual.domainpack.vocabulary-bridges',
                ),
                donor_provenance='control-surface vocabulary follows theory2.tex orchestration chapters',
                citations=('theory2.tex:3332-3414',),
            ),
        )

    def _build_control_surface(
        self,
        *,
        manifest: PackageManifest,
        residuals: tuple[ResidualObligation, ...],
        phase: str,
    ) -> ControlSurface:
        budgets = (
            RouteBudget(
                route=EvidenceRoute.RUNTIME,
                capacity=5,
                reserved=2,
                note='Runtime checks back manifest and session projections.',
            ),
            RouteBudget(
                route=EvidenceRoute.SOLVER,
                capacity=3,
                reserved=1,
                note='Solver capacity is held for future clause-family discharge, not claimed by current CLI output.',
            ),
            RouteBudget(
                route=EvidenceRoute.CONTROLLED_LLM,
                capacity=2,
                reserved=1,
                note='Controlled LLM budget is limited to planning and explanation shaping while residual IDs remain visible.',
            ),
            RouteBudget(
                route=EvidenceRoute.HUMAN,
                capacity=2,
                reserved=1,
                note='Human review remains the channel for migration treaties and vocabulary bridges.',
            ),
        )
        frontier = (
            FrontierNode(
                node_id='frontier.refresh-manifest',
                title='Refresh package manifest projection',
                support_region='package-manifest/runtime',
                expected_route=EvidenceRoute.RUNTIME,
                typed=True,
                scoped=True,
                replay_named=True,
                failure_schema='manifest-shape-mismatch',
                closure_gain=0.8,
                stability_gain=0.9,
                bridge_gain=0.3,
                optionality_gain=0.2,
                cost=0.4,
                overclaim_risk=0.1,
            ),
            FrontierNode(
                node_id='frontier.open-session',
                title='Replay session health opening',
                support_region='interfaces/api-session',
                expected_route=EvidenceRoute.RUNTIME,
                typed=True,
                scoped=True,
                replay_named=True,
                failure_schema='session-open-failure',
                closure_gain=0.7,
                stability_gain=1.0,
                bridge_gain=0.2,
                optionality_gain=0.2,
                cost=0.3,
                overclaim_risk=0.1,
            ),
            FrontierNode(
                node_id='frontier.attach-donor-treaty',
                title='Attach donor migration treaty for theory-derived surface clauses',
                support_region='public-surface/migration',
                expected_route=EvidenceRoute.HUMAN,
                typed=True,
                scoped=True,
                replay_named=False,
                failure_schema='missing-donor-manifest',
                closure_gain=0.5,
                stability_gain=0.8,
                bridge_gain=0.9,
                optionality_gain=0.4,
                cost=0.9,
                overclaim_risk=0.5,
            ),
            FrontierNode(
                node_id='frontier.route-certificates',
                title='Name witness and failure schemas for each evidence route',
                support_region='controller/frontier',
                expected_route=EvidenceRoute.CONTROLLED_LLM,
                typed=True,
                scoped=True,
                replay_named=True,
                failure_schema='route-schema-gap',
                closure_gain=0.6,
                stability_gain=0.7,
                bridge_gain=0.8,
                optionality_gain=0.6,
                cost=0.5,
                overclaim_risk=0.2,
            ),
        )
        generation_state = (
            'surface-projection:'
            f'{manifest.package_name};'
            f'subsystems={len(manifest.subsystem_order)};'
            f'residuals={len(residuals)}'
        )
        return ControlSurface(
            generation_state=generation_state,
            phase=phase,
            budgets=budgets,
            frontier=frontier,
            replay_note='Frontier nodes remain visible only when they name support, route, and falsification schema.',
        )

    def _filtered_residuals(
        self,
        residuals: Iterable[ResidualObligation],
        *,
        kind: str | None = None,
        residual_ids: Iterable[str] | None = None,
    ) -> tuple[ResidualObligation, ...]:
        selected = tuple(residuals)
        if kind is not None:
            selected = tuple(residual for residual in selected if residual.kind.value == kind)
        if residual_ids is not None:
            wanted = set(residual_ids)
            selected = tuple(residual for residual in selected if residual.residual_id in wanted)
        return selected

    def _write_json(self, payload: Mapping[str, Any]) -> None:
        self.context.structured_payload = dict(payload)
        self.context.write_line(json.dumps(payload, indent=2, sort_keys=True))


def _add_common_format_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--format',
        choices=tuple(item.value for item in OutputFormat),
        default=OutputFormat.TEXT.value,
        help='select text or json output',
    )
    parser.add_argument(
        '--phase',
        default='public-reporting',
        choices=('public-reporting', 'alignment', 'stabilization', 'growth'),
        help='phase used for control-surface leverage scoring',
    )


def register_commands(parser: argparse.ArgumentParser) -> None:
    parser_output = getattr(parser, '_output', None)
    subparsers = parser.add_subparsers(
        dest='command',
        parser_class=(
            (lambda *args, **kwargs: HonestArgumentParser(*args, output=parser_output, **kwargs))
            if parser_output is not None
            else argparse.ArgumentParser
        ),
    )

    schema_parser = subparsers.add_parser(
        'judgment-schema',
        help='show the stable schema for JuGeo judgment export payloads',
        description='Project the stable schema for JuGeo judgment and section exports.',
    )
    _add_common_format_argument(schema_parser)

    manifest_parser = subparsers.add_parser(
        'manifest',
        help='show package manifest information with explicit support labeling',
        description='Project the current package manifest without overclaiming stronger support.',
    )
    _add_common_format_argument(manifest_parser)
    manifest_parser.add_argument(
        '--show-capabilities',
        action='store_true',
        help='include capability flags and rationale text from the manifest',
    )
    manifest_parser.add_argument(
        '--include-residuals',
        action='store_true',
        help='include current residual obligations that constrain stronger manifest claims',
    )

    health_parser = subparsers.add_parser(
        'health',
        help='check that an API session opens and report the trust level honestly',
        description='Open an API session and report what the runtime actually observed.',
    )
    _add_common_format_argument(health_parser)
    health_parser.add_argument(
        '--show-scope',
        action='store_true',
        help='display the scope coordinate attached to the health claim',
    )

    residuals_parser = subparsers.add_parser(
        'residuals',
        help='list residual obligations that still constrain public claims',
        description='Residuals remain visible because unresolved work is part of the honest public story.',
    )
    _add_common_format_argument(residuals_parser)
    residuals_parser.add_argument(
        '--kind',
        choices=tuple(kind.value for kind in ResidualKind),
        help='filter residuals by semantic category',
    )
    residuals_parser.add_argument(
        '--include-dependencies',
        action='store_true',
        help='show dependency summaries for each residual obligation in text mode',
    )

    control_parser = subparsers.add_parser(
        'control',
        help='show phase, budget, and frontier information for the CLI control surface',
        description='Expose operational control surfaces as typed futures with replay notes.',
    )
    _add_common_format_argument(control_parser)
    control_parser.add_argument(
        '--only-admissible',
        action='store_true',
        help='suppress frontier nodes that fail the admissibility predicate',
    )

    report_parser = subparsers.add_parser(
        'report',
        help='emit a combined public report containing manifest, claims, residuals, and control',
        description='Produce the broadest public report supported by the current runtime projection.',
    )
    _add_common_format_argument(report_parser)
    report_parser.add_argument(
        '--include-control',
        action='store_true',
        help='include the operational control surface in the report',
    )
    report_parser.add_argument(
        '--include-residuals',
        action='store_true',
        help='include residual obligations in the report',
    )
    report_parser.add_argument(
        '--only-admissible',
        action='store_true',
        help='when control is included, show only admissible frontier nodes',
    )

    explain_parser = subparsers.add_parser(
        'explain',
        help='explain one public claim and cite linked residual obligations',
        description='Explain a public claim without strengthening its support.',
    )
    _add_common_format_argument(explain_parser)
    explain_parser.add_argument('claim_id', help='stable public claim identifier to explain')


def cli_main(argv: Sequence[str] | None = None) -> int:
    return CLIApplication().run(argv)


def main(argv: Sequence[str] | None = None) -> tuple[int, Any | None]:
    application = CLIApplication()
    code = application.run(argv)
    return code, application.context.structured_payload
