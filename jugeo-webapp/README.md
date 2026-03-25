# jugeo-webapp

**Sheaf-theoretic web application synthesis, verification, and ideation.**

A standalone pip-installable module from the [JuGeo](https://github.com/halleyyoung/jugeo) framework that brings algebraic-geometry–inspired verification to full-stack Flask web applications.

## What it does

`jugeo-webapp` treats a Flask web application as a **multi-language sheaf site** — a single connected category whose coordinates span Python, Jinja2, JavaScript, CSS, HTML, and SQL. Bugs that live at the *boundaries* between these languages (missing template variables, API contract mismatches, orphaned CSS classes, broken DOM references) are detected as **descent failures** — local sections that fail to glue into a globally consistent application.

### Core capabilities

| Module | What it provides |
|--------|-----------------|
| **`site`** | Web application site model — 43 coordinate kinds, 26 cross-language morphism kinds, request lifecycle covering families |
| **`parsers`** | Language-specific parsers for Flask routes, Jinja2 templates, JavaScript, CSS, HTML, SQL DDL |
| **`dom`** | DOM as a presheaf on CSS selectors — cascade as descent, specificity computation, media query cohomology |
| **`visual_invariants`** | 6 families of cross-device visual invariants (topological, proportional, threshold, behavioral, structural, conditional) |
| **`cross_language`** | Cross-language reference resolution, overlap checking (10 overlap conditions from the theory), trust topology |
| **`descent`** | Web-specific descent engine, Čech cohomology computation, obstruction catalog |
| **`evidence`** | Multi-channel evidence architecture, cross-language static analysis, security scanning |
| **`trust`** | Web trust topology ("never trust the client" as a descent theorem), trust transport algebra |
| **`fibered`** | Fibered category model — language fibers, cartesian lifts, per-fiber and cross-fiber descent |
| **`ideation`** | 6-stage Flask app ideation pipeline — portfolio construction, coverage analysis, gap detection, analogy transport, novelty scoring, marginal ranking |
| **`generation`** | Flask app generator — routes, models, templates, CSS, JS, tests, blueprints, scaffolding |
| **`rendering`** | Rendering functor R: W → V, visual site model, viewport simulation, interaction modeling |
| **`cli`** | `jugeo webapp` command — full pipeline from ideation to runnable Flask app |

## Installation

```bash
# From the jugeo repository
cd jugeo-webapp
pip install -e .

# With Flask for running generated apps
pip install -e ".[flask]"

# With development dependencies
pip install -e ".[dev]"
```

## Quick start

### Generate a Flask app from the command line

```bash
# Generate a CRUD app
jugeo-webapp --outdir my_app --port 5000 --type crud --name "Task Manager" \
  --models '[{"name": "Task", "columns": [{"name": "title", "type": "string"}, {"name": "done", "type": "boolean"}]}]'

# Run the generated app
cd my_app && python main.py
# Visit http://localhost:5000
```

### Use the ideation pipeline

```bash
# Discover novel Flask app ideas in a domain
jugeo-webapp --ideate --domain "personal finance" --users "freelancers"
```

### Use as a library

```python
from jugeo.webapp.site import WebApplicationSite, WebCoordinateKind
from jugeo.webapp.parsers import FlaskProjectScanner
from jugeo.webapp.cross_language import CrossLanguageAnalyzer

# Scan a Flask project
scanner = FlaskProjectScanner()
project = scanner.scan_project("/path/to/flask/app")

# Run cross-language descent checking
analyzer = CrossLanguageAnalyzer()
report = analyzer.analyze(project.to_dict())

# Report shows descent violations (cross-language bugs)
for violation in report.get("violations", []):
    print(f"  {violation['kind']}: {violation['message']}")
    print(f"  Repair: {violation['repair_hint']}")
```

### Verify visual invariants

```python
from jugeo.webapp.visual_invariants import (
    ThresholdChecker, StructuralChecker, CrossDeviceDescentChecker
)

# Check accessibility invariants
checker = StructuralChecker()
result = checker.check_alt_text(dom)
print(f"Alt text: {'✓' if result.status == 'satisfied' else '✗'}")

# Check cross-device consistency (responsive design bugs)
descent = CrossDeviceDescentChecker()
results = descent.check_descent(invariant_suite, styles_per_device, layout_per_device)
for r in results:
    if not r.globally_consistent:
        print(f"Responsive bug: {r.invariant_id} fails on device overlap")
```

## Theoretical foundation

See [GEOMETRY_OF_WEB_APPLICATIONS.md](../GEOMETRY_OF_WEB_APPLICATIONS.md) for the full theory:

1. **§2**: The multi-language verification site — cross-language morphisms and descent conditions
2. **§3**: The DOM as a presheaf topos — CSS cascade as sheafification, visual invariants as global sections
3. **§4**: Multi-channel evidence architecture — trust topology of the web
4. **§5**: Geometry of purposeful Flask ideation — novelty search in application space
5. **§7**: Fibered category interpretation — language fibers and cartesian lifts

## Package structure

```
src/jugeo/webapp/
├── site/              # Web application site model (8 files)
├── parsers/           # Language-specific parsers (10 files)
├── dom/               # DOM theory + CSS cascade (10 files)
├── visual_invariants/ # 6 invariant families (10 files)
├── cross_language/    # Cross-language analysis (8 files)
├── descent/           # Web descent engine (6 files)
├── evidence/          # Evidence channels (6 files)
├── trust/             # Trust topology (4 files)
├── fibered/           # Fibered category (5 files)
├── ideation/          # App ideation pipeline (12 files)
├── generation/        # Flask app generator (18 files)
├── rendering/         # Rendering functor (6 files)
├── verification/      # Verification pipeline (3 files)
├── cohomology/        # Čech computation (3 files)
└── cli/               # CLI integration (5 files)
```

## Running tests

```bash
pip install -e ".[dev]"
python -m pytest tests/jugeo/webapp/ -q
```

## License

MIT
