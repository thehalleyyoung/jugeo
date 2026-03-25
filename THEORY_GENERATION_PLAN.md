# Theory-Driven Massive Code Generation: A Judgment-Geometry Plan

## The Core Thesis

Every generated line of code is a **local section** over some coordinate in the webapp site.
The generated app is correct if and only if all local sections **glue** — i.e., the descent
conditions imposed by every theory module are simultaneously satisfied.

A theory module does not merely *check* generated code after the fact. It defines:
1. **What the coordinate space looks like** (a Site with objects and morphisms)
2. **What a valid section looks like** (obligations on each coordinate)
3. **What gluing means** (the descent condition — local consistency implies global consistency)
4. **What an obstruction is** (a class in Čech cohomology: a place where local sections fail to agree)

The generator is "correct for a theory" if the global section it produces satisfies that
theory's descent condition. All 35+ theories together define the full correctness criterion.

---

## The Five Layers of Theory and What Each Enforces

### Layer 1: Sites — What Exists and How It Connects

These modules define the **coordinate systems** for each technology. Code generation must
produce content that populates these sites consistently.

#### `sites/dom/` (5 modules)

| Module | What it defines | Correctness criterion |
|---|---|---|
| `kinds.py` | `DOMNodeKind`, `DOMMorphismKind` | Every generated HTML element has a valid node kind; morphisms between elements are structurally valid |
| `coordinate.py` | `DOMCoordinate`, `DOMTreeMorphism` | Every element has a unique coordinate; selector strings parse to coordinates |
| `site.py` | `DOMSite` | The generated DOM tree forms a valid Grothendieck site: children cover parents, references are consistent |
| `presheaves.py` | `DOMValidityPresheaf`, `AccessibilityPresheaf` | Void elements have no children; required attributes are present; accessible names are computable |
| `events.py` | `DOMEvent`, `EventPropagationPath` | Event listeners are attached at valid coordinates; delegation is structurally sound |

**How to use in generation**: Before emitting HTML, run `DOMSite.add_element()` for every
tag. Run `DOMValidityPresheaf.validate_subtree()` on the collected tree. Any obstruction
→ fix the generated HTML. Run `AccessibilityPresheaf.check_required_attrs()` to ensure
`alt`, `for`, `aria-*` attributes are present.

#### `sites/css/` (3 modules)

| Module | What it defines | Correctness criterion |
|---|---|---|
| `specificity.py` | `CSSSpecificity`, `CascadeKey` | The cascade is a total order; no two declarations are genuinely ambiguous |
| `selector_site.py` | `CSSSelectorSite`, `DeadSelectorChecker` | Every selector matches at least one generated element |
| `cascade_pipeline.py` | `CSSInheritanceModel`, `CascadePipeline` | Every property has a computed value; relative units resolve; inheritance is correct |

**How to use in generation**: After generating CSS, run `DeadSelectorChecker.check()`
against the generated DOM. Any selector that matches nothing is dead code — remove it or
fix the HTML. Run `CascadePipeline.specified_value()` for critical properties to verify
they resolve to the intended values.

#### `sites/js/` (2 modules)

| Module | What it defines | Correctness criterion |
|---|---|---|
| `scope_chain.py` | `JSScopeChain`, `ClosureSection` | Every variable reference resolves in the scope chain; no accidental globals; closures capture intended vars |
| `event_loop.py` | `EventLoopSite`, `PromiseSection` | Microtask/macrotask ordering is correct; no race conditions on shared state; async/await suspension points are sound |

**How to use in generation**: Model the generated JS's scope structure in `JSScopeChain`.
Run `resolve_variable()` for every reference. Unresolved → `ReferenceError` in production.
For async code, use `AsyncAwaitDesugaring.desugar_async_function()` to verify each `await`
point is semantically correct.

#### `sites/sql/` (2 modules)

| Module | What it defines | Correctness criterion |
|---|---|---|
| `tables.py` | `SQLRelationalSite`, `QuerySection` | Every FK references an existing table; JOINs are categorical fiber products; N+1 risks are flagged |
| `transactions.py` | `TransactionDescentChecker`, `MigrationMorphism` | Transaction schedules are serializable; migrations are reversible; atomicity is preserved |

**How to use in generation**: Build an `SQLRelationalSite` from the model spec before
generating `models.py`. Run `detect_cycles()` to catch circular FKs. For every ORM query
in the generated code, create a `QuerySection` and run `has_n_plus_1_risk()`. Queries
inside loops are the primary target.

#### `sites/flask/` (1 module)

| Module | What it defines | Correctness criterion |
|---|---|---|
| `routing.py` | `FlaskURLSite`, `FlaskRoute` | Routes cover the URL space; no two routes conflict; blueprint prefixes are unique; orphaned parameters don't exist |

**How to use in generation**: Build a `FlaskURLSite` from the route spec. Run
`detect_conflicts()` before generating `app.py`. Any conflict → disambiguate the routes.
Run `orphaned_parameters()` to find URL params not used in the template.

#### `sites/http/` (1 module)

| Module | What it defines | Correctness criterion |
|---|---|---|
| `request_response.py` | `HTTPStatusCode`, `HTTPExchange` | Every route returns semantically correct status codes; CORS headers are present when needed; caching directives are appropriate |

**How to use in generation**: For each route handler, use `HTTPStatusCode.standard_codes()`
to select the correct response code. GET list views → 200, POST creates → 201, DELETE →
204 (no body), not-found → 404 not 500.

#### `sites/jinja/` (1 module)

| Module | What it defines | Correctness criterion |
|---|---|---|
| `template_site.py` | `JinjaTemplateSite`, `TemplateContextSection` | Every template variable is provided by its route handler; no undefined variable errors at render time; block overrides are consistent |

**How to use in generation**: Build a `JinjaTemplateSite` from the template inheritance
structure. For every route handler, collect the context dict it passes to `render_template`.
Run `context_descent_check()` — if any template variable is missing from the context,
the route handler must be fixed to provide it.

---

### Layer 2: Visual Theory — What the App Looks Like

These modules define correctness for the **rendered output**: layout, color, type, motion.

#### `visual/layout/` (4 modules)

| Module | Correctness criterion |
|---|---|
| `box_model.py` | Every element's padding/border/margin computes correctly; margin collapse doesn't cause layout surprises; overflow obstructions are intentional |
| `flexbox.py` | Flex item sizes sum correctly; shrinkage uses `flex-shrink × flex-basis` (not just flex-shrink); items respect min/max constraints |
| `grid.py` | Fr units only claim space after fixed tracks; named areas cover contiguous cells; auto-placement doesn't produce gaps |
| `responsive.py` | Breakpoints cover the full width range without gaps; properties don't have missing intermediate breakpoint values; font sizes scale appropriately |

**How to use in generation**: When generating CSS for a component, instantiate
`FlexboxSolver` with the intended items and verify sizes. If the intended layout requires
three equal columns of 33%, verify with the grid solver that `1fr 1fr 1fr` in a 960px
container gives 320px each (minus gaps). The solver output constrains the generated CSS
values — don't hardcode pixel values that the solver would contradict.

#### `visual/color/spaces.py`

**Correctness criterion**: WCAG contrast ratio ≥ 4.5:1 for normal text, ≥ 3:1 for large
text. Color values are perceptually uniform where claimed. Color-blind simulations don't
lose critical UI information.

**How to use in generation**: For every text/background pair in the generated CSS, compute
`ColorDistance.wcag_contrast()`. If below threshold, adjust the color (lighten background
or darken text) until it passes. This constrains which hex values the generator may emit.

#### `visual/type/metrics.py`

**Correctness criterion**: Font sizes follow a modular scale; line heights maintain vertical
rhythm; heading sizes decrease monotonically; body text is legible (≥16px base).

**How to use in generation**: Build a `ModularScale` (Perfect Fourth ratio, 16px base).
Assign heading sizes from the scale: h1=scale[4], h2=scale[3], h3=scale[2], etc. Build
a `VerticalRhythm` and verify all generated `line-height` values are multiples of the base
(24px is typical). If `is_rhythmic()` returns False, fix the generated values.

#### `visual/motion/physics.py`

**Correctness criterion**: Transitions use physically correct easing curves; spring
animations settle in < 500ms; no infinite oscillation (overdamped or critically damped for
UI, underdamped for playful elements). Duration × easing is internally consistent.

**How to use in generation**: For every CSS `transition` or JS animation in the generated
code, verify the easing function with `CubicBezier.solve()`. For spring-based JS animations
(e.g., in React/Vue state), use `SpringSystem` to compute settle time before committing to
the parameters.

#### `visual/surface/gradients.py`

**Correctness criterion**: Gradient color stops interpolate correctly in sRGB; layered
backgrounds composite to the intended final color via Porter-Duff; blend modes produce
mathematically correct values.

**How to use in generation**: When generating gradient CSS, verify the midpoint color with
`LinearGradient.color_at(0.5)`. When generating overlapping translucent elements, use
`PorterDuffCompositor.over()` to verify the final color is as intended.

#### `visual/compose/spatial_logic.py`

**Correctness criterion**: Primary content has highest attention weight; related elements
are proximate; visual hierarchy matches information hierarchy; negative space is
intentional (breathing_room > 8px for interactive elements).

**How to use in generation**: After generating a page layout, instantiate `VisualElement`
objects for each component using their CSS coordinates. Run `VisualHierarchy.hierarchy_order()`
and verify the result matches the intended information hierarchy (hero > CTA > navigation >
footer). If not, adjust z-index, size, or position in the generated CSS.

---

### Layer 3: Behavioral Theory — How the App Acts

#### `behavioral/events/continuous_interaction.py`

**Correctness criterion**: Drag targets have sufficient size (≥ 44×44px touch target);
scroll interactions have inertia where expected; pinch-zoom doesn't conflict with page zoom.

**How to use in generation**: For any generated draggable or scrollable element, run
`ContinuousPath` analysis. Verify that scroll containers have `overflow: auto/scroll` and
not `overflow: hidden` (which would trap scroll). Verify touch targets meet size thresholds.

#### `behavioral/forms/validation.py`

**Correctness criterion**: Every form field has validation rules that match server-side
validation; required fields are marked `required`; client and server validation are coherent
(no field that passes client fails server for a structural reason).

**How to use in generation**: Build a `FormSchema` from the model's field definitions.
Run `validate()` on representative edge cases (empty string, 0, None). The generated
client-side validation JS must implement the same rules. Run
`ValidationCoherenceChecker.check_coherence()` with the expected server behavior.

#### `behavioral/network/fetch_theory.py`

**Correctness criterion**: Every AJAX request has a `RetryPolicy`; GET requests are
idempotent; POST requests are not cached; error states are handled for every status class
(4xx, 5xx); no duplicate in-flight requests for the same resource.

**How to use in generation**: For every `fetch()` call generated in JS, instantiate a
`FetchRequest` and check `needs_preflight()` — if True, the server must handle `OPTIONS`.
Verify `CacheStrategy` is appropriate: API GETs use `NETWORK_FIRST` or `STALE_WHILE_REVALIDATE`,
mutations use `NO_STORE`.

#### `behavioral/state/state_presheaf.py`

**Correctness criterion**: No state is both local (component) and global (shared) without
an explicit lift. Derived state always recomputes from base state. Optimistic updates
always have a rollback path. State machines have no unreachable states.

**How to use in generation**: For each piece of application state in the generated JS,
create a `StateVariable`. Run `ComponentStatePresheaf.restrict()` to verify parent→child
props are explicitly passed. Run `StateMachineMode.reachable_states()` for any modal/wizard
flow to verify all states are reachable from the initial state.

#### `behavioral/media/canvas_audio.py`

**Correctness criterion**: Audio graphs have no feedback loops; video has correct buffering
checks before seeking; Canvas sections cover the canvas without uncovered regions.

**How to use in generation**: For any generated `AudioContext` code, build an `AudioGraph`
and run `has_feedback_loop()`. For generated Canvas code, collect `Canvas2DSection` objects
and verify `sections_cover_canvas()`.

---

### Layer 4: Functors — Cross-Language Coherence

#### `functors/python_js_functor.py`

**Correctness criterion**: Python values passed to JS (via JSON API responses or Jinja
template injection) don't lose information. Truthy/falsy semantics are consistent. The
`[]`/`{}` falsy-in-Python / truthy-in-JS trap is explicitly handled.

**How to use in generation**: For every API endpoint that returns JSON, run
`TranslationFidelityChecker.check_value_translation()` on each field type. For every Jinja
variable injected into a `<script>` block, run `TruthinessMapper` to check for semantic
traps. The generator must emit explicit truthiness checks (`if (arr.length > 0)` not
`if (arr)`) for any value that is a Python list or dict.

#### `functors/html_css_js_binding.py`

**Correctness criterion**: Every CSS selector matches at least one generated HTML element;
every `document.getElementById()` call finds an element; no `querySelector()` call returns
null when the code assumes non-null.

**How to use in generation**: After generating HTML and JS together, collect all
`document.getElementById` / `querySelector` calls from the JS using regex. Build
`JSDOMQuery` objects and run `JSHTMLBindingFunctor.null_dereference_risks()`. Any query
that returns null but is used without null check → add a null guard to the generated JS.
Run `CSSBindingFunctor.dead_rules()` to eliminate unused CSS.

---

### Layer 5: Integration — Global Coherence

#### `integration/coherence.py`

**Correctness criterion**: No dead CSS rules; no missing template context variables; no
client fetch to non-existent server routes; XSS risks in template output are noted.

**How to use in generation**: After generating all files (HTML, CSS, JS, Python), run
`WebAppCoherenceEngine.run_all()` with data extracted from all generated files. This is
the final gate before writing output. Any `CoherenceViolation` with severity "error" must
be fixed before the generated files are written to disk.

#### `integration/accessibility.py`

**Correctness criterion**: All WCAG 2.1 Level A criteria are satisfied by generated HTML;
AA criteria are satisfied where possible. Images have alt text; forms have labels; page
has a title; language is declared.

**How to use in generation**: Generate a stub DOM from the HTML template structure. Run
`AccessibilityChecker.check_images()`, `check_form_labels()`, `check_heading_structure()`,
`check_lang_attr()`, `check_page_title()`. Missing `alt=""` on decorative images, or
missing `<label>` for form inputs, must be added to the generated HTML.

#### `integration/security.py`

**Correctness criterion**: POST forms have CSRF tokens; innerHTML usage is absent or
sanitized; SQL queries use parameterized form; admin routes require authentication;
a strict CSP is generated in the Flask config.

**How to use in generation**: Run `CSRFChecker.check_forms()` on every generated `<form>`.
Run `XSSChecker.check_template_output()` for every `{{ variable }}` in Jinja templates —
Jinja's autoescaping handles HTML context but NOT JS context. Run
`SQLInjectionChecker.check_query_construction()` on generated Python. Generate a
`CSPPolicy.flask_default()` and add it to the Flask app config.

#### `engine.py` (TheoryEngine)

**The master orchestrator**. Runs all 20 checks from the 35+ theory modules. Every check
either passes (logged as ✓) or fails with a concrete suggestion. The CLI surfaces warnings
at the end of generation; errors block generation.

---

## The Generation Pipeline: Theory at Every Stage

```
User Prompt: "a blog with posts and comments"
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 1: IDEATION                                               │
│  Input: prompt string                                           │
│  Theory used: none yet (pure NLP/heuristic)                     │
│  Output: AppSpec (name, routes, models, auth, blueprints)       │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 2: SPEC DESCENT CHECK  ← NEW: WebappObligationPresheaf   │
│  Theory used:                                                   │
│    sites/flask/routing → route conflict detection               │
│    sites/sql/tables   → FK cycle detection, PK obligation       │
│    integration/security → auth route obligation                 │
│    sites/http          → method semantics obligation            │
│  Output: DescentResult (pass) or ObstructionReport (fail→fix)   │
│  Principle: local obligations on spec coordinates must glue     │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼ (obstructions → fix spec, or warn user)
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 3: THEORY ENGINE PRE-CHECK  ← TheoryEngine.run_all()     │
│  Theory used: ALL 35 modules (20 checks)                        │
│  Output: TheoryEngineReport with per-module results             │
│  Principle: cross-fiber constraints established before codegen  │
│                                                                 │
│  Key constraints established here:                              │
│    • CSS specificity values for generated selectors             │
│    • Flexbox layout sizes for generated containers              │
│    • Modular scale values for generated font-size CSS           │
│    • Spring settle times for generated transitions              │
│    • WCAG contrast pairs for generated color values             │
│    • Scope chain model for generated JS                         │
│    • SQL fiber products for generated ORM queries               │
│    • Type mappings for generated JSON serializers               │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 4: CONSTRAINED GENERATION                                 │
│                                                                 │
│  app.py generation:                                             │
│    sites/flask/routing → route pattern strings                  │
│    sites/sql/tables   → model field types, FK declarations      │
│    sites/http         → status codes per handler type           │
│    behavioral/forms   → form class field validators             │
│    integration/security → @login_required decoration            │
│                                                                 │
│  templates/*.html generation:                                   │
│    sites/dom/         → valid HTML structure                    │
│    sites/dom/presheaves → required attrs (alt, for, lang)       │
│    sites/jinja        → block definitions, context vars         │
│    integration/accessibility → aria-*, role, tabindex           │
│    integration/security → csrf_token() in POST forms            │
│                                                                 │
│  static/style.css generation:                                   │
│    visual/type/metrics → font-size values from modular scale    │
│    visual/layout/flexbox → flex-grow/shrink values verified     │
│    visual/layout/grid  → fr track sizes verified                │
│    visual/layout/responsive → @media breakpoints covered        │
│    visual/color/spaces → hex values with verified contrast      │
│    visual/motion/physics → transition duration+easing verified  │
│    sites/css/specificity → selector specificity computed        │
│    sites/css/selector_site → selectors match generated DOM      │
│                                                                 │
│  static/app.js generation:                                      │
│    sites/js/scope_chain → variable names don't shadow globals   │
│    sites/js/event_loop  → async/await patterns correct          │
│    functors/python_js_functor → truthy checks for py→js values  │
│    functors/html_css_js_binding → getElementById exists in DOM  │
│    behavioral/events/continuous_interaction → touch targets ≥44px│
│    behavioral/network/fetch → fetch has retry + error handler   │
│    behavioral/state   → state transitions are complete          │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 5: POST-GENERATION DESCENT VERIFICATION                   │
│  Theory used:                                                   │
│    functors/html_css_js_binding → dead CSS, null JS queries     │
│    integration/coherence → template vars, client→server routes  │
│    integration/accessibility → WCAG A violations                │
│    integration/security → CSRF, XSS, missing auth               │
│    sites/jinja → template context satisfaction                  │
│  Output: DescentResult on generated files as a whole            │
│  Principle: local sections (per-file) glue to a global section  │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼ (errors fix generated files; warnings displayed)
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 6: REPORT                                                 │
│  CLI output: theory check results, violation count, suggestions │
└─────────────────────────────────────────────────────────────────┘
```

---

## The Central Sheaf-Theoretic Idea: Descent as Correctness

Each generated file is a **local section** over a coordinate in the webapp site:

- `app.py` → section over `flask.endpoint.*`
- `templates/base.html` → section over `dom.*` ∩ `jinja.*`
- `static/style.css` → section over `css.selector.*`
- `static/app.js` → section over `js.scope.*` ∩ `js.event_loop.*`
- `models.py` → section over `sql.table.*`

The generated app is **globally correct** if and only if these local sections satisfy
**descent**: their values agree on all overlaps (the fiber products between coordinates).

The overlaps that must agree:
- `templates/*.html` ∩ `static/style.css` — CSS selectors must match DOM classes/IDs
- `templates/*.html` ∩ `static/app.js` — JS queries must find DOM elements
- `app.py` ∩ `templates/*.html` — template vars must be in route context
- `app.py` ∩ `models.py` — model classes must match SQL schema
- `static/app.js` ∩ `app.py` — fetch URLs must match Flask routes
- `app.py` routes ∩ `app.py` auth — protected routes have `@login_required`

**An obstruction is a Čech 1-cocycle**: a pair of local sections that agree on their
individual coordinates but disagree on the overlap. Examples:
- CSS selector `.user-card` in `style.css` but `<div class="usercard">` in HTML (selector/DOM overlap mismatch)
- `render_template("post.html", title=post.title)` in Python but `{{ post.author }}` in template (context/template mismatch)
- `fetch("/api/posts")` in JS but no `/api/posts` route in Flask (client/server mismatch)

**Eliminating all obstructions = the generated app compiles, renders, and behaves correctly.**

---

## Theory Module Assignments to Generation Constraints

Each theory module constrains one or more **generatable strings** in the output files.
"Zero dead code" means every module either blocks a generation decision or annotates the output.

| Theory Module | Constrains | Mechanism |
|---|---|---|
| `sites/dom/kinds` | HTML tag choice | `semantic_category()` ensures block/inline distinction |
| `sites/dom/coordinate` | CSS class naming | `from_selector()` validates generated class names |
| `sites/dom/site` | DOM tree structure | Covering family check before writing HTML |
| `sites/dom/presheaves` | `alt`, `for`, `lang`, `aria-*` | Required attr check adds missing attributes |
| `sites/dom/events` | Event listener placement | Delegation validity verified before JS generation |
| `sites/css/specificity` | Selector specificity values | Prevents unresolvable cascade conflicts |
| `sites/css/selector_site` | CSS selector strings | Dead selector removal post-generation |
| `sites/css/cascade_pipeline` | Computed CSS property values | Relative unit resolution in generated CSS |
| `sites/flask/routing` | URL pattern strings | Conflict detection prevents ambiguous routing |
| `sites/http/request_response` | HTTP status code integers | Correct 201/204/404 per handler type |
| `sites/jinja/template_site` | Jinja block names, context dicts | Context descent ensures no undefined vars |
| `sites/js/scope_chain` | JS variable names | Scope analysis prevents shadowing bugs |
| `sites/js/event_loop` | async/await structure | Suspension chain correctness |
| `sites/sql/tables` | SQLAlchemy model field defs | FK validation, N+1 detection |
| `sites/sql/transactions` | Transaction isolation level | Serialization check for concurrent ops |
| `visual/color/spaces` | CSS hex color values | WCAG contrast gating |
| `visual/layout/box_model` | padding/margin/border values | Collapse rules enforced |
| `visual/layout/flexbox` | flex-grow/shrink/basis values | Solver verifies sizes sum correctly |
| `visual/layout/grid` | grid-template-columns values | Fr unit distribution verified |
| `visual/layout/responsive` | @media breakpoint values | Gap-free coverage enforced |
| `visual/motion/physics` | transition-duration + easing | Settle time < 500ms verified |
| `visual/type/metrics` | font-size values | Modular scale constrains all size choices |
| `visual/surface/gradients` | CSS gradient color stop values | Midpoint color verified |
| `visual/compose/spatial_logic` | z-index, position, size values | Attention weight matches info hierarchy |
| `behavioral/events/continuous_interaction` | touch target sizes | 44px minimum enforced |
| `behavioral/forms/validation` | JS validation rules | Coherence with server validation |
| `behavioral/media/canvas_audio` | AudioContext graph | Feedback loop prevention |
| `behavioral/network/fetch_theory` | fetch() arguments, retry logic | Cache strategy, retry backoff |
| `behavioral/state/state_presheaf` | JS state variable declarations | State machine completeness |
| `functors/python_js_functor` | JSON serialization code | Truthy trap guards added |
| `functors/html_css_js_binding` | JS DOM queries | Null-dereference guards added |
| `integration/coherence` | Cross-file consistency | Final gate before file write |
| `integration/accessibility` | WCAG attribute additions | Auto-adds missing a11y attributes |
| `integration/security` | CSRF tokens, XSS guards | Auto-adds `csrf_token()`, guards |

---

## Implementation Roadmap

### Phase 1 (now): Theory modules define what "correct" means
✅ 35+ theory modules, ~25K lines, all subdirs covered
✅ `TheoryEngine` runs all 20 checks in a single call
✅ `theory_conformance.py` integrates with CLI pipeline as DESCENT_CHECK stage

### Phase 2: Constraint propagation — theory informs generation
- **Extend `WebappSpecSite`** to carry theory-derived constraints:
  - `color_constraints: dict` — {selector → (fg_hex, bg_hex)} from color theory
  - `layout_constraints: dict` — {component → FlexLayoutResult} from flexbox solver
  - `typography_constraints: dict` — {heading_level → px_size} from modular scale
  - `animation_constraints: dict` — {transition → (duration_ms, easing)} from spring theory
- **Thread constraints into generators** in `pipeline.py._stage_generate()`:
  - Before writing CSS, run `FlexboxSolver`, `ModularScale`, `ColorDistance.wcag_contrast()`
  - Use their outputs as the actual CSS values (not hardcoded defaults)

### Phase 3: Post-generation correction — theory fixes the output
- After generating all files, run `WebAppCoherenceEngine.run_all()` and
  `SecurityDescentChecker.check_all()` on the generated text
- Auto-fix: add missing `alt=""`, `csrf_token()`, null guards, retry logic
- This makes the generation **self-correcting** rather than just self-checking

### Phase 4: Theory-driven template library
- Each template (minimal/standard/full) is a **pre-verified global section**:
  a set of files that already satisfy all descent conditions by construction
- The generator's job is to instantiate the template with spec-specific values
  while maintaining the pre-verified properties
- New templates are only accepted if they pass all 20 theory checks

### Phase 5: Incremental verification for large codebases
- Each file change triggers only the theory checks that cover that file's coordinates
- Use `Site.restrict_to_subsite()` to run partial descent checks
- Cache descent results per (file, content_hash) pair for sub-second re-checks

---

## What "Correct" Means Across All 35 Theories Simultaneously

A generated Flask/HTML app is **judgment-geometrically correct** if:

1. **Structural correctness** (DOM, Flask, SQL sites): The tree structure is valid, routes don't conflict, models have PKs, FKs resolve.

2. **Visual correctness** (color, typography, layout, motion, surface, Gestalt): Text passes WCAG contrast; font sizes follow a modular scale; flex items size correctly; transitions settle in < 500ms; primary content has highest attention weight.

3. **Behavioral correctness** (events, forms, network, state, media): Forms validate coherently client/server; fetch calls have retry and error handling; state machines have no unreachable states; event propagation matches HTML structure.

4. **Cross-language correctness** (functors): Python truthy/falsy traps are guarded in JS; CSS selectors match the DOM; JS queries find their elements; JSON serialization preserves type semantics.

5. **Global coherence** (integration): Template vars are always provided; CSS doesn't reference missing DOM; fetch URLs have server handlers; WCAG A criteria are satisfied; CSRF tokens are present; XSS is blocked.

Each theory defines a **descent condition** on one fiber of the webapp presheaf. The app is globally correct iff it is locally correct at every fiber — i.e., the global section exists.

This is not aspirational. It is the precise mathematical content of Grothendieck's theorem on effective descent applied to web application generation.
