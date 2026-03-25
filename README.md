# JuGeo

**Judgment Geometry for Software Systems**

JuGeo is a research-driven Python toolkit for reasoning about software with the language of **sites, covers, descent, evidence channels, trust lattices, equivalence, repair, synthesis, orchestration, and research loops**.

At a practical level, the repository gives you three closely related things in one monorepo:

- **Core JuGeo** in `src/jugeo/`: CLI commands, verification flows, generation, descent, evidence, geometry, encodings, orchestration, and research.
- **`jugeo-agents/`**: multi-agent verification and analysis utilities built alongside the main system.
- **Web / app surfaces** including the website in `web/` and webapp-related code in `src/jugeo/webapp/` (plus repo-local app assets such as `jugeo-webapp/` when present in the worktree).

The website, CLI, and Python surface all describe the same broad idea: software systems can be modeled as semantic spaces, checked locally, and then glued into global claims when their overlap conditions agree.

## What JuGeo does

JuGeo spans a wide scope:

- **Verification**: prove properties, check executable specs, inspect trust and evidence, run descent.
- **Bug finding and repair**: surface high-signal Python bugs, then suggest or apply repairs.
- **Relational reasoning**: compare implementations with equivalence-style checks.
- **Generation and orchestration**: synthesize code, run multi-step pipelines, and build projects from high-level prompts.
- **Research / ideation**: generate mathematical directions, run directed research loops, and connect theory back to code.
- **Documentation / public honesty**: compare code and docs with alignment checks.

## Repository layout

A high-level map of the repository:

```text
jugeo/
├── src/jugeo/                # main package
│   ├── cli/                  # command-line entry points
│   ├── geometry/             # sites, covers, descent
│   ├── evidence/             # trust algebra, channels
│   ├── encodings/            # solver / encoding subsystems
│   ├── orchestration/        # synthesis and coordination
│   ├── webapp/               # web application generation/runtime pieces
│   └── ...
├── tests/                    # test suite
├── web/                      # website source
├── jugeo-agents/             # agent-focused companion package/docs/examples
├── examples/                 # runnable examples and experiments
├── experiments/              # research experiments
├── papers/                   # papers, outlines, and theory artifacts
└── README.md                 # this file
```

## Installation

JuGeo is intended to be installed from the repository root.

```bash
git clone https://github.com/thehalleyyoung/jugeo.git
cd jugeo
pip install -e .
python3 -m jugeo --help
```

If your shell entry point is on `PATH`, this also works:

```bash
jugeo --help
```

## CLI overview

The current top-level CLI exposes these commands:

- `foundation`
- `bugs`
- `spec`
- `repair`
- `equiv`
- `evaluate`
- `generate`
- `run`
- `server`
- `load`
- `encode`
- `classify`
- `alignment`
- `mixed`
- `info`
- `test`
- `prove`
- `descend`
- `ideate`
- `catalog`
- `orchestrate`
- `research`

Use `python3 -m jugeo --help` or `python3 -m jugeo <command> --help` for the authoritative flags.

## First commands to run

A practical docs-aligned onboarding path:

```bash
# inspect the installation and loaded subsystems
jugeo info --all
jugeo catalog --count

# find likely issues in a Python file
jugeo bugs mymodule.py

# compare an implementation against a spec file
jugeo prove mymodule.py --spec spec.py
jugeo spec spec.py mymodule.py

# ask for repairs on a failing file
jugeo repair mymodule.py --spec spec.py

# compare two implementations
jugeo equiv left.py right.py

# inspect semantic structure / sections
jugeo load mymodule.py
jugeo descend mymodule.py --strategy eager

# check code vs docs honesty
jugeo alignment mymodule.py --docs README.md
```

## Global CLI flags

The root command supports global flags such as:

- `--verbose` / `-v`
- `--version`
- `--format text|json`
- `--output DIR`
- `--no-llm`
- `--model STR`

Pass them **before** the subcommand, for example:

```bash
jugeo --format json bugs mymodule.py
jugeo --no-llm prove mymodule.py --spec spec.py
```

## Python API: stable docs-safe imports

The safest public imports to document against the current repository are:

- `jugeo.easy`
- `jugeo.geometry.site`
- `jugeo.geometry.covers`
- `jugeo.geometry.descent`
- `jugeo.evidence.trust`
- `jugeo.evidence.channels`

### 1. High-level API with `jugeo.easy`

`jugeo.easy` is the most convenient entry point when you want structured results from the real CLI.

```python
from jugeo.easy import prove, bugs, equiv, ideate

prove_result = prove(
    """
def add(x: int, y: int) -> int:
    return x + y
"""
)
print(prove_result.verdict, prove_result.trust, prove_result.H1)

bug_result = bugs(
    """
def append_item(x, bucket=[]):
    bucket.append(x)
    return bucket
"""
)
print(bug_result.count)

left = "def f(xs): return sorted(xs)"
right = "def f(xs): out = list(xs); out.sort(); return out"
eq_result = equiv(left, right)
print(eq_result.equivalent)

ideas = ideate("judgment geometry for web application verification", n=3)
for theorem in ideas.theorems:
    print(theorem.statement)
```

### 2. Geometry primitives

The geometry layer exposes coordinates, morphisms, covering families, and site builders.

```python
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoveringFamily,
    Morphism,
    MorphismKind,
    SiteBuilder,
)

module = Coordinate(("mymod",), CoordinateKind.MODULE)
validate = Coordinate(("mymod", "validate"), CoordinateKind.FUNCTION)
transform = Coordinate(("mymod", "transform"), CoordinateKind.FUNCTION)

cover = CoveringFamily(
    base=module,
    members=[
        Morphism(validate, module, MorphismKind.RESTRICTION, label="validate→module"),
        Morphism(transform, module, MorphismKind.RESTRICTION, label="transform→module"),
    ],
)

site = (
    SiteBuilder("demo")
    .add_coordinates([module, validate, transform])
    .add_morphisms(cover.members)
    .add_covering_family(cover)
    .build()
)

print(site.coordinate_count())
print(site.morphism_count())
print(site.topology.is_covering(cover))
```

### 3. Covers and descent

```python
from jugeo.geometry.site import Coordinate, CoordinateKind
from jugeo.geometry.covers import Cover, score_cover, refine_cover
from jugeo.geometry.descent import DescentConfiguration, DescentEngine, DescentStrategy

module = Coordinate(("mymod",), CoordinateKind.MODULE)
left = Coordinate(("mymod", "validate"), CoordinateKind.FUNCTION)
right = Coordinate(("mymod", "transform"), CoordinateKind.FUNCTION)

cover = Cover(
    target=module,
    patches=(left, right),
    overlaps=((left.key, right.key),),
    provenance=("readme",),
)

print(score_cover(cover).total_score)
print(refine_cover(cover, suffix="branch").patches[0].path)

engine = DescentEngine(
    configuration=DescentConfiguration(strategy=DescentStrategy.EAGER, record_log=False)
)
report = engine.run(cover, {left.key: {"pure": True}, right.key: {"pure": True}})
print(report.success)
print(report.glued_section)
```

### 4. Trust algebra and evidence channels

```python
from jugeo.evidence.trust import TrustAlgebra, TrustLevel
from jugeo.evidence.channels import (
    ChannelFederation,
    ChannelRouter,
    EvidenceChannel,
    EvidenceRequest,
    EvidenceResponse,
)

algebra = TrustAlgebra()
print(algebra.meet(TrustLevel.SOLVER_DISCHARGED, TrustLevel.COPILOT_SUGGESTED))

request = EvidenceRequest(
    coordinate="mymod.sum_positive",
    proposition="function is pure",
    required_kind="proposal",
    fallback_channels=(EvidenceChannel.COPILOT, EvidenceChannel.HUMAN),
)
responses = [
    EvidenceResponse(
        request_id=request.request_id,
        channel=EvidenceChannel.COPILOT,
        evidence_item={"hint": "tighten precondition"},
        trust_level="proposal",
        provenance=("copilot",),
    ),
    EvidenceResponse(
        request_id=request.request_id,
        channel=EvidenceChannel.HUMAN,
        evidence_item={"review": "approved"},
        trust_level="human_attested",
        provenance=("review",),
    ),
]
merged = ChannelFederation(ChannelRouter()).federate_request(request, responses)
print(merged.channel.value, merged.trust_level)
```

## Monorepo companions

### `jugeo-agents/`

The repository includes `jugeo-agents/`, which extends the same mathematical language to coding-agent teams and multi-agent verification. If your interest is Claude Code / Copilot CLI / Codex style coordination, start there after installing the repo with the same editable environment.

### Web and webapp surfaces

- The documentation website lives under `web/`.
- Webapp-related generation/runtime code lives in `src/jugeo/webapp/`.
- Additional repo-local webapp assets may also exist in `jugeo-webapp/` depending on the worktree.

This repo is therefore not only a verification library; it is also a documentation site, a research workspace, an app-generation environment, and a multi-agent verification monorepo.

## Development notes

- Prefer the CLI help text as the source of truth for command syntax.
- Prefer the public imports listed above when writing examples or docs.
- If you are changing docs, keep examples aligned with `python3 -m jugeo --help` and with importable modules under `src/jugeo/`.

## Useful validation commands

```bash
# CLI surface
python3 -m jugeo --help
python3 -m jugeo prove --help
python3 -m jugeo bugs --help
python3 -m jugeo repair --help
python3 -m jugeo equiv --help
python3 -m jugeo descend --help
python3 -m jugeo ideate --help
python3 -m jugeo info --help

# import checks
python3 - <<'PY'
import jugeo.easy
import jugeo.geometry.site
import jugeo.geometry.covers
import jugeo.geometry.descent
import jugeo.evidence.trust
import jugeo.evidence.channels
print('imports ok')
PY
```

## Website alignment

The root website and this README describe the same project framing:

- **Judgment Geometry for Software Systems**
- broad scope across verification, generation, research, agents, and web systems
- a CLI-first workflow with a small set of safe public Python modules for examples

If you are browsing the site first, the most useful pages to pair with this README are:

- `web/pages/quickstart.html`
- `web/pages/cli.html`
- `web/pages/sites.html`
- `web/pages/descent.html`
- `web/pages/evidence.html`
- `web/pages/architecture.html`

## License / status

This repository is an active research codebase. Interfaces evolve quickly; always prefer the checked-in CLI help and the currently importable public modules over older blog posts or fabricated examples.
