# Judgment-Geometric App Generation: Flask & Static HTML

## The Core Problem Statement

A prompt like `"recipe sharing app"` or `"landing page for a coffee shop"` must
become a complete, correct, high-quality application. "Correct" is multi-layered:

- **Route-level**: every URL has a handler, correct status codes, cache headers
- **Model-level**: FK targets exist, PKs non-null, no N+1 queries
- **Template-level**: valid HTML5, base template extended, CSRF in every POST form
- **CSS-level**: cascade consistent, WCAG contrast, mobile-first, no dead selectors
- **JS-level**: no Python→JS semantic traps, `let`/`const` only, arrow callbacks, CSRF headers
- **Cross-layer**: CSS classes used in HTML are defined; JS `fetch()` targets exist as routes;
  form names match server-side `request.form.get()`
- **Visual**: contrast passes, hierarchy readable, layout works on mobile, CLS < 0.1
- **Security**: XSS-escaped, CSRF-protected, no SQL injection, secure headers

In judgment geometry terms: the app is a **global section** of the webapp presheaf.
Each component (route, model, template, CSS rule, JS module) is a **local section**.
The app is correct iff all local sections **agree on their overlaps** — the descent
condition. Generation = constructing a global section; bugs = descent obstructions.

---

## Two App Types, One Pipeline

The same 8-phase pipeline drives both app types. The difference is which
theory modules are active at each phase:

| Phase | Flask/Jinja2 app | Static HTML app |
|-------|-----------------|-----------------|
| Obligations | Routes + models + auth + templates | Pages + assets + interactions |
| Spec | FlaskURLSite + ORMModel | Page graph + asset manifest |
| Flask layer | app.py, models.py, blueprints | (absent) |
| Template layer | Jinja2 templates + blocks | Plain HTML files |
| CSS layer | Same theory modules | Same theory modules |
| JS layer | fetch() with X-CSRFToken | fetch() without CSRF (no server state) |
| Cross-layer | 8 checks including route↔template | 5 checks (no server routes) |
| Visual | Same theory modules | Same theory modules |

The generator selects a **mode**: `FLASK` or `STATIC`. Theory modules are the
same; which ones are invoked at generation time differs by mode.

---

## The 8-Phase Pipeline

### Phase 0 — Prompt → Obligation Presheaf

**Class**: `PromptObligationExtractor`
**File**: `src/jugeo/webapp/cli/prompt_obligations.py`

Parse natural language into formal obligations using keyword/regex extraction.

```
"recipe sharing app"  →
  domain_nouns:    [recipe, user, ingredient, rating, comment]
  domain_verbs:    [share, create, edit, delete, rate, search]
  user_personas:   [author, viewer]  →  auth_required = True
  app_type:        FLASK (multi-user, server state required)
  data_relations:  recipe ⟶ ingredients (one-to-many)
                   recipe ⟶ ratings     (one-to-many)
                   recipe ⟶ user        (many-to-one FK)
  ui_metaphors:    "sharing" → card grid layout
  implicit:        secure + accessible + responsive + performant

"landing page for a coffee shop"  →
  domain_nouns:    [coffee, menu, location, hours, contact]
  domain_verbs:    [view, read, contact]
  user_personas:   [visitor]  →  auth_required = False
  app_type:        STATIC (no server state, read-only content)
  ui_metaphors:    "landing page" → hero + sections + CTA
  implicit:        accessible + responsive + performant + fast (no server round trip)
```

Each extracted element becomes a `LocalSection` on the **AppSite**.

The `AppSite` is a Grothendieck site whose objects are the conceptual components
(pages, models, routes, CSS modules, JS modules) and whose morphisms are
dependencies between them (template extends, JS imports, CSS includes, FK refs).

---

### Phase 1 — Obligation Coherence Check (Pre-generation descent)

**Theory modules**: `FlaskURLSite`, `ORMFunctorChecker`, `JinjaTemplateSite`,
  `FormSchema`, `SecurityDescentChecker`

Before generating a single line of code, verify the obligation presheaf has
no internal contradictions:

- `FlaskURLSite.validate_routes()` — no duplicate paths/methods
- `ORMFunctorChecker.check_model_integrity()` — all FK targets exist
- `FormSchema` — every POST route has a corresponding form
- `SecurityDescentChecker` — auth routes have login_required
- `JinjaTemplateSite` — template inheritance is a DAG (no cycles)
- For static: page graph is acyclic, asset paths are consistent

This is a **spec-level descent check** — it catches contradictions before
wasting time generating code that can never be consistent.

---

### Phase 2a — Flask Layer Generation (Flask mode only)

**File**: `src/jugeo/webapp/cli/generators/flask_generator.py`

Each line of `app.py` is generated with mandatory theory constraints:

| Theory Module | Constraint Enforced |
|---------------|---------------------|
| `FlaskURLSite` | `<int:id>` converters match model PK type |
| `FlaskContextStack.build_before_request_code()` | Generates `@app.before_request` with auth + CSRF |
| `RoutesCachePolicy.recommend_policy()` | Correct `Cache-Control` header per route |
| `CSRFChecker` | CSRF validation present for all POST/PUT/DELETE |
| `HTTPStatusCode` | 201 for create, 204 for delete, 302 for redirect |
| `SQLTransactionDescentChecker` | All mutations wrapped in `db.session` |

Example generated route (theory annotations inline):

```python
# [FlaskURLSite: <int:id> from ORMModel PK type=Integer]
@app.route('/recipes/<int:id>')
def recipe_show(id: int):
    # [ORMQueryAnalyzer: .get_or_404 avoids N+1; eager-load ingredients]
    recipe = Recipe.query.options(
        db.joinedload(Recipe.ingredients),
        db.joinedload(Recipe.ratings)
    ).get_or_404(id)
    # [RoutesCachePolicy: GET + no auth → private, no-cache]
    response = make_response(render_template('recipes/show.html', recipe=recipe))
    response.headers['Cache-Control'] = 'private, no-cache'
    return response

# [FlaskURLSite: POST handler; CSRFChecker: validated in before_request]
@app.route('/recipes', methods=['POST'])
@login_required
def recipe_create():
    form = RecipeForm(request.form)
    if not form.validate():
        # [HTTPStatusCode: 422 Unprocessable Entity for validation failure]
        return render_template('recipes/form.html', form=form), 422
    recipe = Recipe(title=form.title.data, user_id=current_user.id)
    # [SQLTransactionDescentChecker: explicit transaction]
    db.session.add(recipe)
    db.session.commit()
    # [HTTPStatusCode: 201 Created; Location header]
    return redirect(url_for('recipe_show', id=recipe.id)), 201
```

**Model generation** — `src/jugeo/webapp/cli/generators/model_generator.py`:

| Theory Module | Constraint Enforced |
|---------------|---------------------|
| `ORMModel.to_create_table_sql()` | Valid schema, no nullable PKs |
| `ORMQueryAnalyzer.check_missing_indexes()` | FK columns get `db.Index()` |
| `MigrationDiff` | Schema changes produce Alembic migration stubs |

Generated `models.py` guarantees:
- Every FK column has `db.relationship()` with `back_populates`
- Every FK column has `db.Index(f"ix_{table}_{col}")`
- `created_at` / `updated_at` on every mutable model
- `to_dict()` method for JSON API responses
- `__repr__` for debugging

---

### Phase 2b — Template Layer Generation

**File**: `src/jugeo/webapp/cli/generators/template_generator.py`
**Active for both Flask and Static modes.**

For Flask: generates Jinja2 templates with inheritance.
For Static: generates plain HTML files (same theory, no `{% %}` syntax).

| Theory Module | Constraint Enforced |
|---------------|---------------------|
| `JinjaTemplateSite` | `base.html` with blocks; all pages extend it |
| `DOMValidityPresheaf.validate_subtree()` | No invalid HTML5 nesting |
| `AccessibilityChecker.check_images()` | Every `<img>` has `alt` |
| `AccessibilityChecker.check_form_labels()` | Every `<input>` has `<label>` |
| `AccessibilityChecker.check_heading_structure()` | No skipped heading levels (h1→h2→h3) |
| `AccessibilityChecker.check_lang_attr()` | `<html lang="en">` |
| `AccessibilityChecker.check_page_title()` | Non-empty, descriptive `<title>` |
| `AccessibilityChecker.check_focus_order()` | No positive `tabindex` |
| `CSRFChecker` (Flask only) | Every POST `<form>` has `{{ csrf_token() }}` |
| `FormSchema` | Input `name` attributes match server-side field names |
| `PerformanceChecker.check_image_dimensions()` | All `<img>` have `width` + `height` (CLS) |

Example generated template (Flask):
```html
{% extends "base.html" %}
{# [AccessibilityChecker: non-empty, descriptive title] #}
{% block title %}New Recipe — RecipeShare{% endblock %}
{% block body %}
  {# [AccessibilityChecker: h1 first, no skip] #}
  <h1>Create Recipe</h1>
  {# [CSRFChecker: POST form always includes csrf_token] #}
  <form method="POST" action="{{ url_for('recipe_create') }}">
    {{ form.csrf_token }}
    {# [AccessibilityChecker: label + input pairing; id matches for] #}
    <label for="title">Title</label>
    <input id="title" name="title" type="text"
           required aria-required="true">
    {# [AccessibilityChecker: submit button has visible label] #}
    <button type="submit">Save Recipe</button>
  </form>
{% endblock %}
```

Example generated static HTML:
```html
<!DOCTYPE html>
<html lang="en">  <!-- [AccessibilityChecker: lang attribute] -->
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- [AccessibilityChecker: non-empty title] -->
  <title>Blue Bottle Coffee — Espresso & Pour-Over in SF</title>
  <!-- [CriticalRenderPath: critical CSS inlined; rest deferred] -->
  <style>/* critical above-fold styles */</style>
  <link rel="stylesheet" href="style.css" media="print" onload="this.media='all'">
</head>
<body>
  <!-- [AccessibilityChecker: skip-link for keyboard nav] -->
  <a href="#main" class="skip-link">Skip to content</a>
  <!-- [AccessibilityChecker: landmark role] -->
  <main id="main">
    <!-- [AccessibilityChecker: h1 present, single per page] -->
    <h1>Specialty Coffee, Every Morning</h1>
    <!-- [PerformanceChecker: width+height on every img prevents CLS] -->
    <img src="hero.jpg" alt="A barista pouring latte art" width="1200" height="600">
  </main>
</body>
</html>
```

---

### Phase 2c — CSS Generation

**File**: `src/jugeo/webapp/cli/generators/css_generator.py`
**Active for both Flask and Static modes.**

| Theory Module | Constraint Enforced |
|---------------|---------------------|
| `ModularScale` | All font sizes from ratio (e.g., 1.25 = major third) |
| `ColorDistance.wcag_contrast()` | All text/bg pairs ≥ 4.5:1 (AA) |
| `FlexboxSolver` | Flex layouts computed correct before writing |
| `GridTrackSolver` | Grid layouts use valid track sizing |
| `BreakpointSystem.tailwind_defaults()` | Consistent sm/md/lg/xl breakpoints |
| `ResponsiveLayoutChecker.check_mobile_first()` | `min-width` queries, no gaps 0–639px |
| `CSSSpecificity` | No ID selectors for style; no `!important` |
| `PerformanceBudget` | Total CSS < 50KB |
| `VerticalRhythm` | Line heights are multiples of baseline grid |
| `GestaltAnalyzer` | Primary CTA is visually prominent (size + contrast) |

Generated CSS guarantees:

```css
/* [ModularScale: ratio=1.25, base=16px] */
:root {
  --text-sm:   0.8rem;
  --text-base: 1rem;
  --text-lg:   1.25rem;
  --text-xl:   1.5625rem;
  --text-2xl:  1.953125rem;

  /* [ColorDistance.wcag_contrast(#1a1a1a, #fff) = 18.1:1 ✓ AAA] */
  --color-text:       #1a1a1a;
  --color-bg:         #ffffff;

  /* [ColorDistance.wcag_contrast(#1d5fa8, #fff) = 5.2:1 ✓ AA] */
  --color-primary:    #1d5fa8;

  /* [VerticalRhythm: baseline = 1.5rem = 24px] */
  --rhythm: 1.5rem;
}

/* [ResponsiveLayoutChecker: mobile-first, 1 col] */
.recipe-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--rhythm);
}
/* [BreakpointSystem.tailwind_defaults: sm=640px] */
@media (min-width: 640px) {
  .recipe-grid { grid-template-columns: repeat(2, 1fr); }
}
/* [BreakpointSystem.tailwind_defaults: lg=1024px] */
@media (min-width: 1024px) {
  .recipe-grid { grid-template-columns: repeat(3, 1fr); }
}

/* [GestaltAnalyzer: CTA primary prominence — larger, higher contrast] */
.btn-primary {
  font-size: var(--text-lg);
  background: var(--color-primary);
  color: #fff; /* [ColorDistance: 5.2:1 ✓] */
  padding: 0.75rem 1.5rem;
  /* [WCAGCriterion 2.5.5: min 44px touch target] */
  min-height: 44px;
}
```

---

### Phase 2d — JavaScript Generation

**File**: `src/jugeo/webapp/cli/generators/js_generator.py`
**Active for both Flask and Static modes.**

For Flask: includes X-CSRFToken headers in all mutating fetches.
For Static: fetch() calls have no CSRF (no server-side session).

| Theory Module | Constraint Enforced |
|---------------|---------------------|
| `TranspilationHazardScanner` | No Python→JS semantic traps |
| `ScopingAnalyzer` | `let`/`const` only; no `var`; no global leaks |
| `THIS_BINDING_ANALYSES` | Event callbacks use arrow functions |
| `CSRFChecker` (Flask only) | All `fetch()` mutations include `X-CSRFToken` |
| `EventLoopSite` | `async`/`await` for all I/O; never blocking main thread |
| `NullishCoalescingGuide` | `??` instead of `||` for defaults (avoids 0/""/false traps) |
| `SafeArithmeticPatterns` | Integer division uses `Math.trunc()` |
| `FormValidationCoherenceChecker` | Client validation mirrors server rules |
| `StatePresheaf` | Component state in closed-over `const`, not global vars |

Generated JS (Flask mode):
```javascript
// [ScopingAnalyzer: const/let only, no var]
// [CSRFChecker: read CSRF token from meta tag]
const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content ?? '';

const RatingWidget = (() => {
  // [StatePresheaf: state closed over, not global]
  let currentRating = 0;

  // [THIS_BINDING_ANALYSES: arrow functions throughout — no this-loss]
  const init = () => {
    document.querySelectorAll('.star-btn').forEach(btn => {
      btn.addEventListener('click', (e) => handleRate(e));
    });
  };

  const handleRate = async (e) => {
    // [EventLoopSite: async/await, never blocking]
    // [js_truthiness: ?? not || — score of 0 is valid]
    const score = parseInt(e.target.dataset.score ?? '0', 10);
    // [SafeArithmeticPatterns: parseInt with explicit radix 10]

    try {
      // [CSRFChecker: X-CSRFToken on POST]
      const response = await fetch(`/recipes/${recipeId}/rate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF_TOKEN,
        },
        body: JSON.stringify({ score }),
      });
      // [js_error_handling: always check response.ok]
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      updateDisplay(data.average_rating);
    } catch (err) {
      // [js_error_handling: never swallow errors silently]
      console.error('Rating failed:', err);
      showErrorMessage('Could not save rating. Please try again.');
    }
  };

  return { init };
})();

document.addEventListener('DOMContentLoaded', RatingWidget.init);
```

Generated JS (static mode — no CSRF, no Flask routes):
```javascript
// [EventLoopSite: IntersectionObserver for lazy content]
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    // [js_truthiness: explicit boolean, not truthy check]
    if (entry.isIntersecting === true) {
      entry.target.classList.add('visible');
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.animate-on-scroll').forEach(el => observer.observe(el));
```

---

### Phase 3 — Cross-Layer Descent Verification

**File**: `src/jugeo/webapp/cli/cross_layer_descent.py`

After all files are generated, verify the cross-layer morphisms hold.

**Flask mode — 8 checks:**
```
HTML → CSS:     every class in templates is defined in CSS         (DeadSelectorChecker)
HTML → Flask:   every href/action URL matches a Flask route        (FlaskURLSite)
HTML → JS:      every id/class targeted by querySelector exists    (JSHTMLBindingFunctor)
JS   → Flask:   every fetch(url) matches a Flask route + method    (FlaskURLSite)
Form → Flask:   form field names match request.form.get() calls    (FormSchema)
Tmpl → Model:  {{ obj.field }} attributes exist on ORM model       (ORMModel)
CSS  → HTML:    no dead selectors (defined but never used)         (DeadSelectorChecker)
Auth:           @login_required routes covered by before_request   (FlaskContextStack)
```

**Static mode — 5 checks:**
```
HTML → CSS:     every class is defined in CSS                      (DeadSelectorChecker)
HTML → assets:  every src/href asset file exists on disk           (asset manifest)
JS   → HTML:    every querySelector target id/class exists         (JSHTMLBindingFunctor)
CSS  → HTML:    no dead selectors                                  (DeadSelectorChecker)
Links:          every internal href points to an existing page     (page graph)
```

Each failure is a `DescentObstruction` with a precise repair action:

```
Obstruction: CSSClassMissing(".btn-danger", in="templates/confirm.html")
Repair:      css_generator.add_rule(".btn-danger { background:#dc2626; color:#fff; }")

Obstruction: JSFetchEndpointMissing("POST /api/recipes/search", in="app.js")
Repair:      flask_generator.add_route("GET", "/api/recipes/search", "search_recipes")

Obstruction: FormFieldMismatch(html_name="recipe_title", server_name="title")
Repair:      template_generator.rename_field("recipe_title", "title")

Obstruction: TemplateFieldMissing("recipe.summary", model="Recipe")
Repair:      model_generator.add_column(Recipe, "summary", TEXT, nullable=True)
```

---

### Phase 4 — Visual Quality Pass

**File**: `src/jugeo/webapp/cli/visual_correctness.py`
**Active for both Flask and Static modes.**

| Check | Theory Module | Pass Criterion |
|-------|--------------|----------------|
| Text contrast | `ColorDistance.wcag_contrast()` | ≥ 4.5:1 (AA) |
| Large text contrast | `ColorDistance.wcag_contrast()` | ≥ 3:1 |
| Font scale coherence | `ModularScale` | All sizes are ratio multiples |
| Mobile layout | `ResponsiveLayoutChecker` | No gaps 0–639px, mobile-first |
| Heading hierarchy | `AccessibilityChecker` | No skipped h-levels |
| Image CLS | `PerformanceChecker.check_image_dimensions()` | All `<img>` have width+height |
| Font FOUT | `PerformanceChecker.check_font_loading()` | `font-display: swap` |
| Focus style | `WCAGCriterion 2.4.7` | Visible `:focus` style present |
| Touch targets | `WCAGCriterion 2.5.5` | Interactive elements ≥ 44×44px |
| Render path | `CriticalRenderPath.blocking_resources()` | ≤ 2 render-blocking resources |
| Core Web Vitals | `CoreWebVitals.estimate_from_path()` | LCP < 2500ms, CLS < 0.1 |

---

### Phase 5 — Iterative Repair Loop

```python
MAX_REPAIR_ITERATIONS = 5

def generate_with_repair(spec, mode):
    app = generate_all_layers(spec, mode)
    for iteration in range(MAX_REPAIR_ITERATIONS):
        obstructions = cross_layer_descent_check(app, mode)
        if not obstructions:
            break
        for obs in obstructions:
            apply_repair(app, obstruction_to_repair(obs))
    visual_obs = visual_quality_pass(app)
    for obs in visual_obs:
        apply_visual_repair(app, obs)
    return app
```

This is the sheaf-theoretic heart: each repair is a **section extension** —
extending the global section to cover a missing local piece until all local
sections agree on their overlaps.

---

## What Makes This Different from Template Generation

| Aspect | Current (Template-Based) | Theory-Driven |
|--------|-------------------------|---------------|
| Prompt → spec | Fixed template ignoring prompt | Obligation extraction from domain vocabulary |
| Routes | Hard-coded CRUD | `FlaskURLSite` with correct converters |
| Models | Generic fields | `ORMModel` with FK indexes, timestamps |
| HTML validity | Whatever template says | `DOMValidityPresheaf` enforced |
| Accessibility | Not checked | `AccessibilityChecker` WCAG 2.1 AA |
| CSS colors | Hard-coded hex | `ColorDistance.wcag_contrast()` ≥ 4.5:1 |
| CSS fonts | Hard-coded rems | `ModularScale` ratio-based |
| CSS mobile | May or may not work | `ResponsiveLayoutChecker` mobile-first |
| JS callbacks | `obj.method` (loses `this`) | Arrow functions per `THIS_BINDING_ANALYSES` |
| JS truthiness | `\|\|` for defaults (traps 0/"") | `??` per `NullishCoalescingGuide` |
| Forms | Template forms | CSRF + labels + server-matching names |
| Cross-layer | Not checked | `CrossLayerDescentChecker` closes the loop |
| Security | Template may omit | `SecurityDescentChecker` + `CSRFChecker` mandatory |
| Static app | (not supported) | Same pipeline, Flask layer omitted |

---

## Implementation Order (each step is a small agent ~300 lines)

1. `prompt_obligations.py` — `PromptObligationExtractor`, `AppObligationPresheaf`
2. `spec_builder.py` — `SpecBuilder` using obligation presheaf + theory validation
3. `generators/flask_generator.py` — routes + before_request with theory constraints
4. `generators/model_generator.py` — ORM models with FK indexes + migrations
5. `generators/template_generator.py` — Jinja2 + static HTML with accessibility
6. `generators/css_generator.py` — ModularScale + WCAG contrast + responsive
7. `generators/js_generator.py` — arrow callbacks + async/await + CSRF
8. `cross_layer_descent.py` — `CrossLayerDescentChecker` (5 checks static, 8 Flask)
9. `visual_correctness.py` — `VisualCorrectnessChecker` wrapping visual theory
10. `pipeline.py` update — wire all 8 phases; add `mode=FLASK|STATIC`

Each generator is ~300 lines. Each does ONE thing with ONE set of theory modules.
No agent writes more than one generator file.
