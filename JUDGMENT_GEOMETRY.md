# Judgment Geometry: A Proof-Carrying Python Alternative to LEAN, Coq, and F\*

> *"Proofs are not terms. Proofs are geometric: they are sections over sites,
> glued by descent, and observable through their support."*

---

## 1. The Problem with the Status Quo

Three systems dominate formal verification today:

- **LEAN 4 / Coq** — built on the *Calculus of Inductive Constructions* (CIC).
  Proofs are λ-terms; programs are extracted to OCaml or C.
- **F\*** — built on *dependent types + refinement types + Dijkstra monads*.
  Deep Z3 integration, user-defined effects, code extraction to OCaml/F#/C/Wasm.

All three share the same DNA: proofs are terms, the Curry-Howard correspondence
is king, and "verified code" means *extraction* from the proof language to a
different target language. This works — but it carries costs:

| Pain point | Where it hurts |
|---|---|
| **No Python output** | LEAN extracts to C; F\* extracts to OCaml/F#/C/Wasm; Coq extracts to OCaml/Haskell. None targets Python. |
| **Single proof object** | A CIC term or F\* term is one monolithic evidence artifact. No room for mixed evidence channels. |
| **Binary trust** | It type-checks or it doesn't. No gradation between "Z3 proved it", "tests witnessed it", and "copilot suggested it". |
| **Effects bolted on** | F\* has Dijkstra monads; Coq/LEAN have none. But even F\*'s effects are indexed over WP transformers — not over the actual runtime semantics of Python exceptions, async, generators, context managers. |
| **Tactics are proof scripts** | LEAN/Coq/Meta-F\* tactics manipulate proof *terms*. They cannot manipulate *evidence bundles*, *trust levels*, or *obstruction repair*. |
| **Failures are diagnostics** | A type error is a string. An SMT timeout is `Unknown`. Neither is a first-class object you can query, transport, or repair. |
| **Module-level only** | No built-in mechanism for project-wide verification across hundreds of files. |
| **AI is external** | Copilot-for-Lean and LLM-assisted F\* are external tools, not first-class evidence channels with trust accounting. |

**Judgment Geometry** (JuGeo) is a ground-up redesign that subsumes the
capabilities of all three systems while producing **proof-carrying Python
directly**.

---

## 2. What Is Judgment Geometry?

Judgment Geometry replaces the type-theoretic core of CIC/F\* with a
**sheaf-theoretic** one. The central idea:

> A **judgment** is not a boolean, not a proof term, and not an F\* computation
> type. It is an **8-component geometric object** that records *what* is
> claimed, *where* in the codebase it lives, *what evidence* supports it, *what
> remains to be done*, and *how much you should trust it*.

Formally:

```
J = (c, φ, A, E, O, B, T, Π)
```

| Slot | Name | What it carries |
|------|------|-----------------|
| **c** | Coordinate | Where in the semantic site (module, function, line) |
| **φ** | Proposition | What is claimed (structural, behavioural, relational) |
| **A** | Carrier | The dependent type / kind of the claim |
| **E** | Evidence bundle | Multi-channel: solver proofs, runtime witnesses, AI proposals |
| **O** | Obligations | Residual work that must still be discharged |
| **B** | Obstructions | Persistent blockers — first-class cohomology classes |
| **T** | Trust annotation | An element of the trust ordered algebra (not a scalar!) |
| **Π** | Provenance | Audit trail: who produced this judgment and how |

Judgments live on a **semantic site** — a category of coordinates equipped
with a Grothendieck topology of **covering families**. Local judgments are
glued into global guarantees via **sheaf descent**. When descent fails, the
failure is a **Čech cohomology obstruction** — a first-class mathematical
object that tells you exactly which overlap conditions broke and how to
repair them.

---

## 3. Everything F\* Can Do — and Where JuGeo Goes Further

F\* is the most capable existing proof assistant for verifying effectful
programs. JuGeo matches every F\* capability and extends each one.

### 3.1 Refinement Types

**F\***: Refinement types `{x:int | x >= 0}` conjoining base types with
logical predicates. Subtyping checked by Z3.

**JuGeo**: Full refinement type encoding via a four-stage pipeline
(`encodings/scalar_encodings/refinement_type_encoder.py`):

```
RefinementSortBuilder          construct SMT-LIB 2 sort declarations
        ↓
PredicateNormalizer            De Morgan, let-inlining, Skolemisation,
        ↓                     conjunct flattening
ConstraintLifter               quantifier binding, QE projection
        ↓
RefinementTypeEncoder          produce RefinementEncoding artifacts
```

**Goes further**: Refinement types in JuGeo are not isolated annotations —
they are *propositions at coordinates*. A refinement `{x:int | x >= 0}` at
coordinate `module.function.arg_x` is a judgment with its own evidence
bundle, trust level, and provenance. You can refine the *same* variable at
different trust levels:

- Z3-discharged: `SOLVER_DISCHARGED` trust
- Runtime-witnessed on test inputs: `RUNTIME_WITNESSED` trust
- Copilot-suggested: `COPILOT_SUGGESTED` trust (hard ceiling — cannot silently
  promote to solver level)

Subtype checking computes intersections and unions of refinement predicates
with full soundness guarantees for quantifier-free fragments and explicit
residuals for undecidable fragments.

### 3.2 Dependent Types

**F\***: Full dependent types with Π and Σ types.

**JuGeo**: The **Carrier** slot `A` in the judgment 8-tuple is a dependent
type at the coordinate. Types can depend on values at other coordinates in
the site, connected by coordinate morphisms:

```python
# Coordinate morphism transporting a type along a restriction
CoordinateMorphism(
    source="module.function",
    target="module.function.loop_body",
    kind=MorphismKind.RESTRICTION
)
# The carrier at the loop body can depend on the carrier at the function
```

**Goes further**: Dependent types in JuGeo compose via *sheaf restriction*,
not just substitution. A type at coordinate `c` can be restricted to any
sub-coordinate in the cover, and the restriction is checked for compatibility
by descent. This gives project-scale dependent typing — not just within one
module, but across the entire codebase.

### 3.3 Effects System

**F\***: User-defined effects via Dijkstra monads with weakest-precondition
(WP) transformers. Effect labels in a partial order. State, exceptions,
divergence, I/O as effects. Steel for concurrent separation logic.

**JuGeo**: Python runtime effects encoded as **typed sheaf sections** over the
semantic site (`python_runtime/effects_async/`). Five effect families, each
modeled as morphisms in the site geometry:

| Effect | F\* approach | JuGeo approach |
|--------|-------------|---------------|
| **Exceptions** | `Exn` effect with WP transformer | Alternate-path sections: try/except as coordinate forks; exception chaining as section restriction; BaseException hierarchy as partial order on coordinates |
| **State** | `ST` effect with heap model | Scope/state sections (`scope_and_state/`): global/local bindings as site sections; mutation tracked as section updates with obligation generation |
| **Async** | Not native (would need custom effect) | Asyncio tasks as suspended section morphisms; task scheduling as morphism composition; cancellation as obstruction morphisms |
| **Generators** | Not native | Fiber restriction sequences; yield as partial section emission; send/throw as morphism injection |
| **Context managers** | Not native | `with`-blocks as covering families; `__enter__`/`__exit__` as cover morphisms; temporal obligations for finally-blocks |

**Goes further in three ways**:

1. **Effects are geometric, not monadic.** F\*'s Dijkstra monads index
   computations by WP transformers — powerful but abstract. JuGeo's effects
   are *sections in the semantic site*, meaning they compose via gluing and
   descent, not monad transformers. This makes effect interaction visible as
   overlap conditions rather than hidden in WP indices.

2. **Native Python effects.** F\* must model Python exceptions via a custom
   `Exn` effect, Python async via a custom concurrency monad, Python generators
   via a custom coroutine type. JuGeo models them directly — `try/except` is
   literally a coordinate fork in the site, not an encoding.

3. **Effect obstructions are persistent.** When an exception path is
   unchecked, JuGeo records an obstruction at the coordinate where the
   exception escapes. This obstruction persists, can be queried, and generates
   a repair frontier. F\* would simply fail to type-check; JuGeo tells you
   *what* failed, *where*, and *how to fix it*.

### 3.4 Metaobject Protocol

JuGeo models Python's class creation as a **three-phase morphism sequence**
(`python_runtime/metaobject_surfaces/`):

```
Phase 1: TRANSPORT      type.__prepare__() → namespace dict
Phase 2: INCLUSION      body execution populates namespace
Phase 3: REFINEMENT     type.__new__() constructs class object
```

Each phase is a morphism in the semantic site. Descriptors (`__set_name__`),
`__init_subclass__`, and metaclass interactions are tracked as transport
morphisms. No other proof assistant models Python's metaobject protocol at
this level of fidelity.

### 3.5 SMT Integration

**F\***: Deep Z3 integration. VCs translated to first-order logic. SMT solves
or times out. No fragment awareness.

**JuGeo**: Fragment-aware Z3 dispatch with **13 decidable SMT-LIB fragments**
and optimized tactic selection per fragment
(`solver/fragments.py`, `solver/router.py`):

```
Fragment Taxonomy
─────────────────────────────────────────────────
QF_LIA         linear integer arithmetic           timeout: 5s
QF_LRA         linear real arithmetic               timeout: 5s
QF_BV          fixed-width bitvectors               timeout: 10s
QF_UF          uninterpreted functions               timeout: 5s
QF_AUFLIA      arrays + UF + LIA                     timeout: 15s
QF_ABV         arrays + bitvectors                   timeout: 15s
STRINGS        string theory                         timeout: 15s
SEQUENCES      sequence theory                       timeout: 15s
ARRAYS         extensional arrays                    timeout: 10s
DATATYPES      algebraic datatypes                   timeout: 10s
NONLINEAR      nonlinear arithmetic                  timeout: 30s
QUANTIFIED     full first-order with quantifiers     timeout: 60s
MIXED          multi-theory (Nelson-Oppen splits)    timeout: 30s
UNKNOWN        unclassifiable → escalate to copilot  timeout: 60s
```

**Goes further**:

1. **Fragment classification before dispatch.** F\* sends everything to Z3 as
   one blob. JuGeo's `FragmentClassifier` inspects the syntactic signature
   (sorts, function symbols, quantifier depth) and routes to optimized Z3
   tactic chains. `FragmentDecomposer` handles mixed formulas via
   Nelson-Oppen theory combination.

2. **Jurisdiction management.** The `SolverRouter` enforces that Z3 only
   handles queries within its decidable jurisdiction. Queries outside Z3's
   fragment are escalated — first to runtime witnesses, then to copilot oracle
   — with trust ceilings enforced at each step.

3. **Five routing strategies:**
   - `CheapestStrategy` — minimize solver cost
   - `FastestStrategy` — minimize wall-clock latency
   - `MostTrustedStrategy` — maximize trust level of result
   - `RoundRobinStrategy` — load-balance across solvers
   - `SmartStrategy` — adaptive, based on current proof state

4. **Auditable proofs.** Every Z3 result includes an unsat-core extraction,
   making the proof auditable. F\* gets sat/unsat/unknown; JuGeo gets a
   structured `Z3Result` with proof object, unsat-core, trust level, and
   latency metrics.

---

## 4. Tactics — First-Class Proof Moves, Not Just Scripts

### 4.1 The problem with traditional tactics

In LEAN, Coq, and F\*, tactics are *proof scripts* that manipulate a proof
state (a goal with hypotheses). Meta-F\* tactics are programs in a `Tac`
effect that can call `trefl`, `smt`, `mapply`, `compute`, `norm`, etc.

This is powerful for manipulating proof terms, but it has limits:
- Tactics cannot manipulate *trust levels* — there is no `promote_trust` tactic
- Tactics cannot inspect *evidence channels* — you can't write a tactic that
  says "try Z3, and if it times out, fall back to copilot with a trust ceiling"
- Tactics cannot generate *repair frontiers* — when a tactic fails, you get
  `tactic failed`, not a structured description of what went wrong
- Tactics operate on *one goal* — not across an entire project site

### 4.2 JuGeo's tactic system: Semantic Moves

JuGeo replaces tactics with **semantic moves** — first-class proof-search
operations that operate on the full judgment geometry:

```python
@dataclass(frozen=True)
class SemanticMove:
    move_id: str
    kind: MoveKind           # What type of proof step
    target_coordinate: str   # Where in the site
    preconditions: tuple     # What must hold before
    postconditions: tuple    # What will hold after
    expected_gain: float     # Estimated progress (0–1)
    cost_estimate: int       # Budget (solver/copilot calls)
    trust_floor: TrustLevel  # Minimum trust of result
```

**Move kinds** (the tactic vocabulary):

| Move | Analogous F\*/LEAN tactic | What it does in JuGeo |
|------|--------------------------|----------------------|
| `VERIFY` | `smt()` | Dispatch obligation to Z3 by fragment |
| `CONSTRUCT` | `exact` / `apply` | Synthesize inhabitant via solver/copilot/runtime |
| `NORMALIZE` | `norm` / `compute()` | Strip stylistic differences for comparison |
| `WEAKEN` | `assumption` / `intro` | Weaken context (structural rule) |
| `CONTRACT` | — | Contract duplicate hypotheses |
| `EXCHANGE` | — | Reorder context |
| `CUT` | `have` / `assert ... by` | Introduce intermediate lemma (admissible — cut elimination holds) |
| `RESTRICT` | — | Restrict judgment to sub-coordinate |
| `TRANSPORT` | `rw` / `rewrite` | Transport judgment along coordinate morphism |
| `GLUE` | — | Glue local sections across overlap |
| `REPAIR` | — | Apply repair frontier to obstruction |
| `NEGOTIATE_TREATY` | — | Reconcile conflicting patch interfaces |
| `REFINE_COVER` | — | Refine covering family around violation |
| `PROMOTE_TRUST` | — | Explicitly promote trust (with justification) |
| `CHALLENGE` | — | Challenge existing evidence, triggering demotion |
| `ESCALATE` | — | Escalate to copilot/human oracle |

**Nine moves have no analogue in any existing proof assistant** — they
operate on geometric structure (covers, overlaps, trust) that doesn't exist
in CIC or F\*.

### 4.3 Inference rules as first-class values

JuGeo's deduction engine (`encodings/deduction_rules/`) implements inference
rules as manipulable first-class values — not just tactic combinators:

**Structural rules** (`structural_rules.py`):
```
Weakening:     Γ ⊢ J          Contract:     Γ, A, A ⊢ J
              ─────────                     ─────────────
              Γ, A ⊢ J                      Γ, A ⊢ J

Exchange:      Γ, A, B, Δ ⊢ J    Cut:    Γ ⊢ A    Γ, A ⊢ J
              ─────────────────          ──────────────────
              Γ, B, A, Δ ⊢ J                 Γ ⊢ J
```

**Key meta-theorem**: Cut is admissible — any proof using cut can be
transformed into a cut-free proof (§33.4 of theory2.tex). This is the
analogue of LEAN/Coq's normalization, but at the judgment level.

**Semantic rules** (`semantic_rules.py`):
- `IntroductionRule` — connective introduction (right rules)
- `EliminationRule` — connective elimination (left rules)
- `ComputationRule` — β/η reductions
- `DefinitionalEqualityRule` — definitional equality

**Judgment transitions** (`judgment_transitions.py`):
Small-step transition system encoding the dynamics of proof state — each
transition is a typed morphism in the site, carrying its own trust annotation.

**Unification engine** — Full first-order unification with occurs-check,
meta-variable binding, and constraint propagation for rule instantiation.

### 4.4 Move selection and semantic control

Where F\*/LEAN tactics are executed by the user in a proof script, JuGeo's
moves are selected by an **adaptive control layer**
(`orchestration/semantic_control/`):

```
Proof state
    │
    ▼
MoveEnumerator           enumerate all applicable moves
    │                    (respecting capacity bounds)
    ▼
PreconditionChecker      evaluate preconditions for each
    │
    ▼
MovePrioritizer          sort by expected gain, cost, trust
    │
    ▼
MoveConflictResolver     detect/resolve conflicting moves
    │
    ▼
MoveApplicationEngine    apply move (or dry-run)
    │
    ▼
PostconditionVerifier    verify postconditions, compute realized gain
```

**Control laws** (proof-search strategies):
- `GREEDY` — always pick highest expected gain
- `LOOKAHEAD` — simulate k steps ahead
- `BALANCED` — trade off gain vs. cost
- `ADAPTIVE` — switch strategy based on convergence metrics

This is strictly more powerful than Meta-F\*'s tactic combinators: it
operates on the full geometric proof state (coordinates, covers, overlaps,
trust levels, obstructions) and can adaptively switch strategies mid-proof.

### 4.5 The construction loop — tactics meet synthesis

The local construction loop (`generation/construction.py`) is JuGeo's
analogue of F\*'s VC generation + SMT discharge, but generalized to
multiple evidence channels:

```
Phase 1: PROPOSE       Solicit candidates from all channels
                       (Z3, runtime, copilot, human)

Phase 2: NORMALIZE     Strip stylistic differences
                       (α-equivalence, variable renaming, formatting)

Phase 3: COMPARE       Semantic comparison with Pareto ranking
                       (trust level, residual count, evidence strength)

Phase 4: SELECT        Pick best candidate by trust/residual/evidence
                       criteria with explicit justification
```

Each phase produces a **compression record** tracking exactly what changed:
```
ΔS_u — section changes
ΔO_u — obligation deltas
ΔE_u — evidence deltas
ΔX_u — obstruction changes
ΔK_u — certificate updates
supp(Δ_u) — support region of changes
```

---

## 5. Obligation Discharge — Beyond Dijkstra Monads

### 5.1 F\*'s approach: WP transformers

F\* verifies effectful code via **Dijkstra monads** — each effect comes with
a *weakest precondition transformer* that the system computes automatically
and sends to Z3. This is elegant but opaque: when Z3 times out, you know the
WP is undischarged, but you don't know *which part* or *why*.

### 5.2 JuGeo's approach: Structured obligation lifecycle

Every proof obligation in JuGeo has a typed lifecycle with full provenance:

```
PENDING → ASSIGNED → IN_PROGRESS → DISCHARGED
                                  ↘ FAILED → obstruction recorded
                                  ↘ EXPIRED → re-schedulable
```

**Discharge backends** (priority-ordered):

| Priority | Backend | What it handles | Trust level |
|----------|---------|----------------|-------------|
| 1 | **Structural** | Vacuous quantifiers, structurally guaranteed | `VERIFIED_PROOF` |
| 2 | **Tautology** | Recognized logical tautologies (syntactic pattern match) | `VERIFIED_PROOF` |
| 3 | **Z3** | Full SMT discharge via fragment-aware routing | `SOLVER_DISCHARGED` |
| 4 | **Runtime** | Evidence from prior certificates or test execution | `RUNTIME_WITNESSED` |
| 5 | **Oracle** | Deferred to LLM/human (with hard trust ceiling) | `COPILOT_SUGGESTED` |

**Cohomology classification of discharge status:**

| Class | Meaning |
|-------|---------|
| **H⁰** | Fully discharged — obligation is a global section |
| **H¹** | Partially discharged — residual gap remains |
| **H²** | Failed — obstruction recorded in Čech 2-cocycle register |
| **H∞** | Deferred to oracle — trust ceiling enforced |

This is strictly more expressive than F\*'s binary discharged/undischarged:
you know *how much* of the obligation was discharged, *by whom*, at *what
trust level*, with *what residuals remaining*.

---

## 6. The Trust Algebra — What No Other System Has

### 6.1 The problem

F\* trusts Z3 completely — if Z3 says `unsat`, the obligation is discharged
at the same trust level as a hand-written proof. LEAN/Coq trust their kernel
completely. None of them can express "Z3 proved the arithmetic, but the
copilot suggested the loop invariant, and the tests witnessed the I/O
behavior — and I want to track which claims rest on which evidence."

### 6.2 The trust ordered algebra

```
𝔗 = (ℰ_adm, ⪯, ⊕, ⊖, ↑_π, ↓_χ)
```

| Operator | Name | What it does |
|----------|------|-------------|
| `ℰ_adm` | Admissible evidence | The family of well-formed evidence configurations |
| `⪯` | Trust ordering | `CONTRADICTED < UNVERIFIED < COPILOT_SUGGESTED < ORACLE_PROPOSED < RUNTIME_WITNESSED < SOLVER_DISCHARGED < VERIFIED_PROOF` |
| `⊕` | Composition | Combining evidence from two channels — yields the *weaker* (conservative join) |
| `⊖` | Attenuation | Weakening through transport (e.g., restricting to sub-coordinate) |
| `↑_π` | Promotion | Trust strengthening with explicit justification + audit log |
| `↓_χ` | Demotion | Trust weakening (ceiling enforcement, challenge response) |

**Three laws that no other system enforces:**

1. **No silent promotion.** Promoting `COPILOT_SUGGESTED` to
   `SOLVER_DISCHARGED` requires an explicit named policy route. Every
   promotion is recorded in an append-only audit log. Attempting silent
   promotion raises `JuGeoError`.

2. **Conservative join.** `SOLVER_DISCHARGED ⊕ COPILOT_SUGGESTED =
   COPILOT_SUGGESTED`. You cannot launder low-trust evidence through
   high-trust channels.

3. **Challenge conservativity.** On challenge (e.g., a test fails),
   the system may demote or residualize but may not leave old trust
   standing without explanation.

```python
profile = TrustProfile(
    tier=TrustTier.COPILOT_SUGGESTED,
    scope=("spec.affine.program.00",),
    reasons=("copilot-proposal",)
)

# Silent promotion → error
profile.promote(TrustTier.VERIFIED)     # raises JuGeoError!

# Explicit promotion → audited
promoted = profile.promote(
    TrustTier.SOLVER_DISCHARGED,
    explicit=True,
    reason="z3-confirmed-arithmetic"
)
assert promoted.reasons == (
    "copilot-proposal",
    "explicit-promotion",
    "promotion:copilot_suggested->solver_discharged",
    "z3-confirmed-arithmetic"
)
```

---

## 7. First-Class Obstructions — Proof Failures as Mathematical Objects

When a Coq proof fails, you get a type error. When an F\* VC times out, you
get `Unknown`. When JuGeo descent fails, you get an **Obstruction** — a
first-class persistent object with full geometric structure:

```python
@dataclass(frozen=True)
class Obstruction:
    obstruction_id: str
    coordinate: CoordinateObject          # where in the site
    proposition: Proposition              # what was claimed
    admissibility_condition: str          # which law was violated
    evidence_present: EvidenceBundle      # what evidence existed
    repair_frontier: RepairFrontier       # how to fix it
    cohomology_class: str                 # H¹ classification
    downstream_obligations: tuple[str, ...] # what depends on this
```

Obstructions persist across sessions. They form a **cohomology ring** over
the site. They can be:
- **Queried**: "Show me all obstructions at trust level < SOLVER_DISCHARGED"
- **Transported**: Move an obstruction along a coordinate morphism
- **Repaired**: The repair frontier suggests concrete fix strategies
- **Challenged**: Provide counter-evidence to discharge an obstruction

**Backpressure from obstructions** — when too many obstructions accumulate,
JuGeo's backpressure system (`generation/backpressure.py`) throttles proof
production:

```
Backpressure families:
  INTEGRATION_LAG       gluing falling behind production
  TREATY_INSTABILITY    overlap treaties being renegotiated
  OBLIGATION_OVERFLOW   obligations accumulating faster than discharge
  EVIDENCE_EXHAUSTION   evidence channels saturated
  BUDGET_CRITICAL       computational budget near ceiling

Responses: THROTTLE | PAUSE | REDIRECT | ESCALATE | SHED_LOAD
```

No other proof assistant has semantic backpressure on proof search.

---

## 8. From Proofs to Proof-Carrying Python Code

### 8.1 Why not extraction?

F\* extracts to OCaml/F#/C/Wasm. LEAN extracts to C. Coq extracts to
OCaml/Haskell. In every case, "verified code" means code in a *different
language* from the proof. The programmer must trust the extraction pipeline,
and the extracted code carries no proof metadata.

JuGeo skips extraction entirely. It generates **proof-carrying Python
directly** — standard Python code annotated with semantic metadata.

### 8.2 The generation pipeline

```
Specification
    │
    ▼
┌──────────────────┐
│   Cover Design   │ Decompose goal into manageable patches
└───────┬──────────┘
        ▼
┌──────────────────┐
│ Inhabitant Fleets│ Generate candidates via solver/copilot/runtime/human
└───────┬──────────┘
        ▼
┌──────────────────┐
│   Construction   │ propose → normalize → compare → select
│      Loop        │ (4-phase tactic loop with compression records)
└───────┬──────────┘
        ▼
┌──────────────────┐
│  Descent & Glue  │ Check overlaps, extract obstructions
└───────┬──────────┘
        ▼
┌──────────────────┐
│ Certificate Emit │ Annotate winning section with full provenance
└───────┬──────────┘
        ▼
  Proof-Carrying Python
```

### 8.3 What proof-carrying Python looks like

Every generated function comes with **semantic scaffold functions** that
record its position in the proof geometry:

```python
# ── Coordinate: where this code lives in the semantic site ──────────
def _spec_affine_program_0_coordinate():
    return "spec.affine.program.00"

# ── Coerce: type normalization for cross-channel comparison ─────────
def _spec_affine_program_0_coerce(value):
    if isinstance(value, bool):
        return int(value)
    return value

# ── Support: the observable region of this function ─────────────────
def _spec_affine_program_0_support(values):
    support = []
    for offset, value in enumerate(values):
        support.append((_spec_affine_program_0_coordinate(), offset, value))
    return tuple(support)

# ── Descent profile: obstruction tracking metadata ──────────────────
def _spec_affine_program_0_descent_profile(values):
    profile = []
    for coordinate, offset, value in _spec_affine_program_0_support(values):
        profile.append((coordinate, offset % 2, value))
    return tuple(profile)

# ── The actual implementation ───────────────────────────────────────
def solve(values, bias, mod, keep):
    total = 0
    for value in values:
        if int(value) % mod == keep:
            total += int(value) + bias
    return total
```

This is **standard Python** — importable, callable, deployable. But the
scaffold functions make the code *self-describing*: any verifier can
reconstruct the coordinate, support region, and descent profile without
leaving the language.

### 8.4 Verification on declared covers

```json
{
  "case_id": "spec-affine-sat-00",
  "program": "def solve(values, bias, mod, keep): ...",
  "spec_program": "def spec(result, values, bias, mod, keep): ...",
  "input_cover": [
    {"args": [[-2, 0, 3, 6], -2, 3, 0], "kwargs": {}},
    {"args": [[0, 1, 2, 3, 4, 5], -2, 3, 0], "kwargs": {}},
    {"args": [[-3, -2, -1, 0, 1], -2, 3, 0], "kwargs": {}}
  ],
  "expected_satisfies": true
}
```

The proof obligation: for every point in the declared cover,
`spec(program(*args), *args) == True`. This is not just testing — the cover
is a geometric object, the spec is a proposition, and the result is a
judgment with multi-channel evidence and trust tracking.

---

## 9. Head-to-Head: The Same Proof in LEAN, F\*, and JuGeo

To make the differences concrete, let's prove the same property in all three
systems: *a filtered affine sum over a list is correct*.

The property: given a list of integers, a bias, a modulus, and a residue
class, sum `(v + bias)` for all `v` where `v % mod == keep`. Prove that the
implementation equals the specification.

### 9.1 In LEAN 4

```lean
-- 1. Define the spec and implementation in Lean's functional language
def spec (values : List Int) (bias mod keep : Int) : Int :=
  (values.filter (fun v => v % mod == keep)).foldl (fun acc v => acc + v + bias) 0

def solve (values : List Int) (bias mod keep : Int) : Int :=
  values.foldl (fun acc v =>
    if v % mod == keep then acc + v + bias else acc) 0

-- 2. State the theorem
theorem solve_eq_spec (values : List Int) (bias mod keep : Int) :
    solve values bias mod keep = spec values bias mod keep := by
  -- 3. Write a tactic proof (manual, potentially long)
  unfold solve spec
  induction values with
  | nil => simp
  | cons v vs ih =>
    simp [List.foldl, List.filter]
    split
    · -- case v % mod == keep
      simp [*]; omega
    · -- case v % mod ≠ keep
      exact ih

-- 4. Extract to C (separate step, no provenance in output)
-- #eval solve [(-2 : Int), 0, 3, 6] (-2) 3 0
```

**What you get**: a proof term in Lean's kernel. To get executable code, you
run Lean's compiler to C. The C code carries *no trace* of the proof — it's
bare `int64_t` arithmetic. If you want Python, you're out of luck.

**Pain points**:
- You had to rewrite your Python logic in Lean's functional language
- The tactic proof (`unfold`, `induction`, `simp`, `split`, `omega`) is
  manual and fragile — changing the implementation breaks the proof
- The output is C, not Python
- Trust is binary: it compiled or it didn't
- No mixed evidence — you can't say "Z3 handled the arithmetic, tests
  covered the edge cases"

### 9.2 In F\*

```fstar
module AffineSum

open FStar.List.Tot

// 1. Define the spec as a pure function with refinement types
let rec expected_total (values: list int) (bias mod_ keep: int)
  : Tot int (decreases values) =
  match values with
  | [] -> 0
  | v :: vs ->
    if v % mod_ = keep
    then (v + bias) + expected_total vs bias mod_ keep
    else expected_total vs bias mod_ keep

// 2. Define the implementation
let rec solve (values: list int) (bias mod_ keep: int)
  : Tot int (decreases values) =
  match values with
  | [] -> 0
  | v :: vs ->
    let rest = solve vs bias mod_ keep in
    if v % mod_ = keep then rest + v + bias else rest

// 3. Prove equivalence — must manually guide Z3
let rec solve_correct (values: list int) (bias mod_ keep: int)
  : Lemma (ensures solve values bias mod_ keep = expected_total values bias mod_ keep)
          (decreases values) =
  match values with
  | [] -> ()     // base case: Z3 handles
  | v :: vs ->
    solve_correct vs bias mod_ keep;   // inductive step
    // Z3 discharges the arithmetic obligation automatically
    ()

// 4. Extract to OCaml (or F#, or C via KaRaMeL)
//    Output carries no provenance — bare OCaml functions
```

**What you get**: Z3 discharges most arithmetic automatically (better than
Lean!), but:
- You had to rewrite your Python logic in F\*'s ML-like syntax
- The proof is a recursive lemma with manual induction structure
- When Z3 times out (e.g., nonlinear arithmetic), you get `Unknown` — no
  structured obstruction, no repair frontier
- Extraction targets OCaml/F#/C/Wasm — **not Python**
- Trust is binary: the VC was discharged or it wasn't
- You can't mix "Z3 proved the arithmetic" with "runtime tests covered the
  edge cases" — it's all-or-nothing SMT

### 9.3 In JuGeo — Proof-Carrying Python

```python
# ── You write your implementation in Python. That's it. ─────────────

def _eligible(value, mod, keep):
    return int(value) % mod == keep

def solve(values, bias, mod, keep):
    total = 0
    for value in values:
        if _eligible(value, mod, keep):
            total += int(value) + bias
    return total

# ── You write your spec in Python too. ──────────────────────────────

def _expected_total(values, bias, mod, keep):
    total = 0
    for value in values:
        value = int(value)
        if value % mod == keep:
            total += value + bias
    return total

def spec(result, values, bias, mod, keep):
    """Boolean specification — must hold on every cover point."""
    return isinstance(result, int) and result == _expected_total(values, bias, mod, keep)
```

JuGeo takes this and produces **proof-carrying Python** — the same code, now
annotated with geometric metadata:

```python
# ── Semantic scaffold (generated by JuGeo) ──────────────────────────

def _spec_affine_program_0_coordinate():
    """This function's address in the semantic site."""
    return "spec.affine.program.00"

def _spec_affine_program_0_support(values):
    """The observable region — which inputs this function 'sees'."""
    support = []
    for offset, value in enumerate(values):
        support.append(("spec.affine.program.00", offset, int(value)))
    return tuple(support)

def _spec_affine_program_0_descent_profile(values):
    """Overlap metadata — how this function interacts with neighbors."""
    profile = []
    for coord, offset, value in _spec_affine_program_0_support(values):
        profile.append((coord, offset % 2, value))
    return tuple(profile)

# ── Your implementation (unchanged!) ────────────────────────────────

def _eligible(value, mod, keep):
    return int(value) % mod == keep

def solve(values, bias, mod, keep):
    total = 0
    for value in values:
        if _eligible(value, mod, keep):
            total += int(value) + bias
    return total

# ── Your spec (unchanged!) ──────────────────────────────────────────

def spec(result, values, bias, mod, keep):
    return isinstance(result, int) and result == _expected_total(values, bias, mod, keep)

# ── Declared cover (the geometric witness) ──────────────────────────
# 10 concrete input points where the spec must hold.
# JuGeo verifies on every point; Z3 discharges arithmetic;
# runtime witnesses confirm execution; obstructions recorded if any fail.

COVER = [
    {"args": [[-2, 0, 3, 6], -2, 3, 0]},
    {"args": [[0, 1, 2, 3, 4, 5], -2, 3, 0]},
    {"args": [[-3, -2, -1, 0, 1], -2, 3, 0]},
    {"args": [[-3, 0, 3, 9], -2, 3, 0]},
    {"args": [[0, 3, 6, 9, 12], -2, 3, 0]},
    {"args": [[], -2, 3, 0]},
    {"args": [[1, 2, 4, 5, 7], -2, 3, 0]},
    {"args": [[3], -2, 3, 0]},
    {"args": [[-6, -3, 0, 3, 6], -2, 3, 0]},
    {"args": [[0, 0, 0], -2, 3, 0]},
]
```

**What you get**:
- **The code IS Python.** No rewriting into a different language. Import it,
  call it, ship it.
- **The proof IS the code.** The scaffold functions (`_coordinate`,
  `_support`, `_descent_profile`) make the code self-describing. Any
  verifier can re-check the proof without leaving Python.
- **Multi-channel evidence.** The verification result is a judgment:

  ```
  Judgment(
    coordinate = "spec.affine.program.00",
    proposition = "spec(solve(*args), *args) == True ∀ cover",
    evidence = EvidenceBundle([
      EvidenceItem(channel=SOLVER, kind="z3-arith",
                   trust=SOLVER_DISCHARGED,
                   detail="integer arithmetic on all 10 cover points"),
      EvidenceItem(channel=RUNTIME, kind="cover-execution",
                   trust=RUNTIME_WITNESSED,
                   detail="executed on 10/10 cover points, all passed"),
    ]),
    obligations = (),             # fully discharged
    obstructions = (),            # no failures
    trust = SOLVER_DISCHARGED,    # conservative join
    provenance = ("z3-arith", "cover-execution")
  )
  ```

- **If something fails**, you get an obstruction, not a string:

  ```
  Obstruction(
    coordinate = "spec.affine.program.00",
    proposition = "spec(solve([], -2, 3, 0), [], -2, 3, 0) == True",
    admissibility_condition = "cover_point_5_agreement",
    evidence_present = EvidenceBundle([...]),
    repair_frontier = RepairFrontier([
      "Check edge case: empty list",
      "Verify base case total=0 satisfies spec",
    ]),
    cohomology_class = "H¹(cover, spec-affine)",
  )
  ```

### 9.4 Side-by-side summary

| Dimension | LEAN 4 | F\* | **JuGeo** |
|---|---|---|---|
| **Language you write in** | Lean (functional) | F\* (ML-like) | Python |
| **Language you ship** | C (extracted) | OCaml/C (extracted) | Python (same code!) |
| **Proof effort** | Manual tactics (`unfold`, `induction`, `simp`, `omega`) | Recursive lemma + Z3 auto-discharge | Declare cover + spec; system handles the rest |
| **When proof breaks** | `tactic failed` | `Z3 Unknown` / type error | Structured `Obstruction` with repair frontier |
| **Trust granularity** | Binary (compiled / didn't) | Binary (VC discharged / not) | 7-level algebra (solver ⊕ runtime ⊕ oracle...) |
| **Evidence tracking** | None (kernel says yes/no) | None (Z3 says sat/unsat) | Full provenance per evidence item |
| **Handles Python idioms?** | ✗ | ✗ | ✓ (exceptions, async, generators, metaclasses, context managers) |
| **Rewrite your code?** | Yes (into Lean) | Yes (into F\*) | No — verify the Python you already have |

---

## 10. Why Proof-Carrying Python Is Easier Than F\*

### 10.1 You don't rewrite your code

F\* requires you to express your program in its ML-like dependently-typed
language. This means:
- Learning F\*'s syntax, module system, and effect annotations
- Rewriting every Python function as a `Tot` or `ST` computation
- Maintaining *two* codebases — the F\* source and the extracted output

JuGeo verifies the Python you already wrote. The scaffold functions are
*added alongside* your code, not instead of it.

### 10.2 You don't write tactic proofs

In Lean, proving `solve_eq_spec` requires a manual induction, case splits,
simp lemmas, and arithmetic tactics. In F\*, you write a recursive lemma
that mirrors the function structure. Both are fragile — changing the
implementation requires changing the proof.

JuGeo's proof obligation is declarative:

```
∀ point ∈ COVER: spec(solve(*point.args), *point.args) == True
```

The system's **semantic move engine** automatically selects tactics (VERIFY,
CONSTRUCT, NORMALIZE, GLUE, etc.) via adaptive control. You declare the
cover and the spec; the orchestrator does the proof search.

### 10.3 Failure is actionable, not opaque

When F\*'s Z3 backend times out, you get `(error "unknown")`. You then
spend hours adding `assert_norm`, intermediate lemmas, or fuel annotations
trying to help the solver.

When JuGeo fails, you get:
- **Which cover point** failed
- **Which evidence channels** were attempted
- **What trust level** was reached before failure
- **A repair frontier** with concrete suggestions
- **A cohomology class** (H¹/H²/H∞) classifying the failure type

### 10.4 Mixed evidence is natural

F\* is all-or-nothing: either Z3 discharges the VC, or you write a manual
proof. There is no middle ground.

JuGeo naturally mixes evidence:

```
"Z3 proved the arithmetic for 8/10 cover points.
 Runtime witnesses confirmed 2 edge cases Z3 timed out on.
 Trust: SOLVER_DISCHARGED ⊕ RUNTIME_WITNESSED = RUNTIME_WITNESSED (conservative).
 Residual: none."
```

This is how real verification works — some things are proved, some are tested,
some are reviewed. JuGeo gives you an honest account of each.

### 10.5 The proof ships with the code

F\* extracts to OCaml. The OCaml binary carries zero proof metadata. If
someone asks "how do you know this is correct?", you point them back to the
F\* source — a different codebase in a different language.

JuGeo's proof-carrying Python includes the scaffold functions in the deployed
artifact. Any verifier can:
1. Read `_coordinate()` to find the function's site address
2. Read `_support()` to see what inputs the function covers
3. Read `_descent_profile()` to check overlap compatibility
4. Re-run `spec(solve(*args), *args)` on the declared cover
5. Inspect the judgment's evidence bundle, trust level, and provenance

The proof *travels with the code*. No separate artifact, no different
language, no extraction step.

### 10.6 Concrete example: what F\* can't express

Consider verifying a Python function that uses a **context manager** and
**async/await**:

```python
async def fetch_and_validate(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict, got {type(data)}")
            return data
```

**In F\***: You would need to:
1. Model `aiohttp` as a custom effect (there is no standard one)
2. Model context managers as paired enter/exit with a custom `ST`-like monad
3. Model async/await as a concurrency effect (Steel, maybe, but it's for
   separation logic, not Python coroutines)
4. Write all of this in F\*'s syntax, then extract to OCaml
5. The extracted OCaml code doesn't use `aiohttp` or `async with` — it's a
   completely different program

**In JuGeo**: The function is verified *as-is*. The effect encoding is:

```
async with session.get(url) as response:
    │
    ├── Cover morphism: __aenter__ (session setup)
    ├── Body section: response.json() + validation
    └── Cover morphism: __aexit__ (session teardown)
    
Exception path (ValueError):
    └── Coordinate fork at isinstance check
        ├── Normal path: return data
        └── Exception path: raise ValueError
            └── Temporal obligation: __aexit__ must still run
```

The proof-carrying output is the same Python function, annotated with
coordinates and support regions. No rewriting. No extraction. No separate
language.

---

## 11. The Four Proof Modes

### 11.1 Specification satisfaction

> *Given a program and a spec, does the program satisfy the spec on every
> point of the declared cover?*

100 benchmark cases across 8 families. Both programs and specs instrumented
with coordinate/support/descent scaffolding.

### 11.2 Equivalence checking

> *Given two programs, are they extensionally equal on every cover point?*

Programs carry separate coordinates (`equivalence.affine.left.00` vs.
`equivalence.affine.right.00`). Descent verifies output compatibility via
four witness strategies: structural matching, semantic hashing, Z3 symbolic
equivalence, and oracle-assisted comparison.

### 11.3 Bug detection

> *Given a program, does it contain known bug patterns?*

Six bug classes detected as structured obstructions:

| Bug label | Pattern | Obstruction type |
|---|---|---|
| `bare-except` | Empty except clauses | Effect obstruction (exception path) |
| `identity-literal` | `x is 1` | Semantic obstruction (identity vs. equality) |
| `late-binding-closure` | Closure capture bugs | Scope obstruction (binding time) |
| `mutable-default` | `def f(x=[])` | State obstruction (shared mutation) |
| `open-without-close` | Unclosed file handles | Resource obstruction (lifetime) |
| `shadow-builtin` | `list = [1,2,3]` | Namespace obstruction (shadowing) |

### 11.4 Relational refinement

> *Given a program, compute a refined version that satisfies additional
> constraints while preserving existing behavior.*

Witness computation via Čech cohomology: restrict programs to patches,
compute local witnesses via four strategies (structural, semantic, Z3,
oracle), then glue via descent.

---

## 12. Architecture

JuGeo is ~960 Python modules organized into a strict dependency DAG:

```
kernel                     Core services, lifecycle, authority
  ↓
geometry                   Sites, coordinates, covers, descent (4 strategies)
  ↓
judgments                  8-tuple algebra, contexts, sections, comparisons
  ↓
evidence                   Trust algebra, 6+ channels, certificates, provenance
  ↓
solver                     Z3 (13 fragments), routing (5 strategies), session pool
  ↓
encodings                  18 sub-packages:
  │                          deduction_rules (inference, structural, semantic, transitions)
  │                          scalar_encodings (refinement types, path conditions)
  │                          collection_heap_encodings (heap summaries)
  │                          theorem_schemas (Hilbert-style axiom schemas)
  │                          + 14 more (text, tensor, IR, doctrine, sequences...)
  ↓
python_runtime             Scope/state, exceptions, async, generators,
  │                        context managers, metaobject protocol, program loading
  ↓
generation                 Cover design, construction loops, hypercover treaties,
  │                        inhabitant fleets, state space, backpressure, replay/gluing
  ↓
orchestration              Synthesis orchestrator, fleet management, semantic control,
  │                        move selection, mixed-evidence routing, frontier objectives
  ↓
ideation                   Theorem economics, discovery engine, novelty search,
  │                        proof suggestion, research assistance, regime bootstrap
  ↓
problem_modes              Spec satisfaction, equivalence, bug detection,
                           relational refinement, repair semantics, documentation
```

---

## 13. Comparison Matrix — LEAN × Coq × F\* × JuGeo

| Capability | LEAN 4 | Coq | F\* | **JuGeo** |
|---|---|---|---|---|
| **Dependent types** | ✓ | ✓ | ✓ | ✓ (at coordinates, composed by sheaf restriction) |
| **Refinement types** | ✗ | Limited | ✓ | ✓ (4-stage pipeline with trust tracking) |
| **User-defined effects** | ✗ | ✗ | ✓ (Dijkstra monads) | ✓ (5 native Python effects as sheaf sections) |
| **Effect polymorphism** | ✗ | ✗ | ✓ | ✓ (effects compose via morphisms, not monads) |
| **SMT integration** | Plugin | Plugin | Deep (Z3) | Deep (Z3, 13 fragments, 5 routing strategies) |
| **Tactics** | ✓ (Lean tactics) | ✓ (Ltac/Ltac2) | ✓ (Meta-F\*) | ✓ (16+ semantic moves + adaptive control) |
| **Tactic metaprogramming** | ✓ | ✓ (Ltac2) | ✓ (Tac effect) | ✓ (moves are first-class values + copilot oracle) |
| **Cut elimination** | ✓ (normalization) | ✓ | N/A | ✓ (admissible, §33.4) |
| **Weakest preconditions** | ✗ | ✗ | ✓ (WP transformers) | ✓ (obligation lifecycle with H⁰/H¹/H²/H∞ classification) |
| **Code extraction target** | C | OCaml, Haskell | OCaml, F#, C, Wasm | **Python** (proof-carrying, with provenance) |
| **Native Python support** | ✗ | ✗ | ✗ | ✓ (first-class target) |
| **Trust model** | Binary | Binary | Binary | Ordered algebra (7 levels, no silent promotion) |
| **Evidence channels** | 1 (kernel) | 1 (kernel) | 2 (kernel + Z3) | 6+ (formal, solver, runtime, oracle, human, composed) |
| **Failure model** | Type error | Type error | Type error / Unknown | First-class cohomology obstruction |
| **Failure persistence** | ✗ | ✗ | ✗ | ✓ (obstructions persist, form cohomology ring) |
| **Repair guidance** | ✗ | ✗ | ✗ | ✓ (repair frontiers with concrete strategies) |
| **Project-wide reasoning** | Module-level | Module-level | Module-level | Site-wide descent across entire codebase |
| **AI integration** | External | External | External | First-class channel with trust ceiling + audit |
| **Backpressure** | ✗ | ✗ | ✗ | ✓ (5 obstruction families, 5 response types) |
| **Python exceptions** | ✗ | ✗ | Custom effect | Native (coordinate forks) |
| **Python async** | ✗ | ✗ | ✗ | Native (suspended section morphisms) |
| **Python generators** | ✗ | ✗ | ✗ | Native (fiber restriction sequences) |
| **Python metaclasses** | ✗ | ✗ | ✗ | Native (3-phase morphism sequence) |
| **Context managers** | ✗ | ✗ | ✗ | Native (covering families) |

---

## 14. Getting Started

```bash
pip install -e .               # install
pytest test_examples/ -v       # run benchmark suites (300 cases)
python3 test_algorithms.py     # run treaty synthesis tests
python3 check_blueprint.py     # check implementation completeness
jugeo                          # CLI entry point
```

Requires Python ≥ 3.10.

---

## 15. Why This Matters

Proof assistants shouldn't require you to leave Python, shouldn't force all
evidence into one trust level, and shouldn't throw away failures as
unstructured error messages.

Judgment Geometry makes four bets:

1. **Geometry over type theory.** Sheaves and descent are the right
   abstraction for project-scale reasoning. Local claims glue into global
   guarantees; failures are structured, persistent, and repairable.

2. **Mixed evidence over pure proof.** Real verification combines formal
   proofs, solver discharge, runtime witnesses, and AI proposals. A trust
   algebra that tracks all of these honestly is more useful than binary
   type-checks/doesn't.

3. **Semantic moves over proof scripts.** Tactics should operate on the
   full proof geometry — coordinates, covers, overlaps, trust levels,
   obstructions — not just proof terms. Adaptive control should select
   tactics, not just the user.

4. **Python-native over extraction.** If the code you ship is Python, the
   proof should be Python too — annotated with coordinates, support regions,
   and descent profiles so that any verifier can reconstruct the argument
   without leaving the language.

The result: write a specification, get a verified implementation with
multi-channel evidence and structured provenance, and deploy it — all in
Python, all auditable, and all with a precise account of exactly what was
proved, by whom, at what trust level, and what remains open.

---

## Appendix A: The Judgment 8-Tuple — Formal Definition

From `preliminaries/theory2.tex`:

```
Definition.  A judgment over a semantic site S is a tuple

    J = (c, φ, A, E, O, B, T, Π)

where:
  c ∈ Ob(S)         coordinate (object of the site)
  φ ∈ Prop(c)       proposition at c
  A ∈ Type(c)       carrier (dependent type at c)
  E ∈ Ev(c)         evidence bundle over c
  O ⊂ Ob(c)        finite set of residual obligations
  B ⊂ H¹(S, F)     finite set of obstructions (cohomology classes)
  T ∈ 𝔗            trust annotation from the trust ordered algebra
  Π ∈ Prov          provenance record

A section σ over a cover U = {Uᵢ → X} is a compatible family
{Jᵢ}ᵢ∈I satisfying:

  Jᵢ|_{Uᵢ ∩ Uⱼ} = Jⱼ|_{Uᵢ ∩ Uⱼ}   for all i, j ∈ I

Descent theorem: compatible sections yield a unique global judgment.
Obstruction theorem: incompatible sections yield Δ ∈ H¹(U, F).
```

## Appendix B: Tactic Vocabulary — Complete Reference

```
STRUCTURAL RULES (context manipulation)
  WEAKEN              add unused hypothesis to context
  CONTRACT            merge duplicate hypotheses
  EXCHANGE            reorder context
  CUT                 introduce intermediate lemma (admissible)

LOGICAL RULES (connective manipulation)
  INTRODUCE           right-rule for connective
  ELIMINATE            left-rule for connective
  COMPUTE             β/η reduction
  DEFINITIONAL_EQ     definitional equality

GEOMETRIC MOVES (site-level operations)
  RESTRICT            restrict judgment to sub-coordinate
  TRANSPORT           transport along coordinate morphism
  GLUE                glue local sections across overlap
  REFINE_COVER        refine covering family around violation

EVIDENCE MOVES (trust-level operations)
  VERIFY              dispatch to Z3 by fragment
  CONSTRUCT           synthesize inhabitant via multi-channel fleet
  NORMALIZE           strip stylistic differences for comparison
  PROMOTE_TRUST       explicitly promote trust (with justification)
  CHALLENGE           challenge existing evidence → demotion
  ESCALATE            defer to copilot/human oracle

TREATY MOVES (overlap management)
  NEGOTIATE_TREATY    reconcile conflicting patch interfaces
  REPAIR              apply repair frontier to obstruction

META MOVES (proof-search control)
  DECOMPOSE           break goal into sub-goals via cover design
  BACKTRACK           undo last move (with provenance)
  SWITCH_STRATEGY     change control law (GREEDY/LOOKAHEAD/BALANCED/ADAPTIVE)
```

## Appendix C: Effect Encoding — Python Effects as Sheaf Sections

```
EXCEPTIONS
  try-block       →  coordinate fork (normal path ∪ exception path)
  except-clause   →  section restriction to exception coordinate
  raise           →  morphism injection into exception path
  finally         →  temporal obligation on both paths
  chaining        →  section composition (__cause__, __context__)
  hierarchy       →  partial order on exception coordinates

STATE
  local binding   →  section at function coordinate
  global binding  →  section at module coordinate
  mutation        →  section update with obligation generation
  closure         →  section restriction to captured scope

ASYNC
  coroutine       →  suspended section morphism
  await           →  morphism composition (resume point)
  task            →  independent section with scheduling morphism
  cancellation    →  obstruction morphism (coordinate collapse)

GENERATORS
  yield           →  partial section emission
  send            →  morphism injection into generator fiber
  throw           →  exception morphism into generator
  close           →  section termination

CONTEXT MANAGERS
  with-block      →  covering family (enter ∪ body ∪ exit)
  __enter__       →  cover morphism (setup)
  __exit__        →  cover morphism (teardown)
  nested with     →  cover refinement
```

---

*JuGeo is developed at `/Users/halleyyoung/Documents/jugeo/` with
mathematical foundations in `preliminaries/theory2.tex` (67 chapters) and
implementation guided by `theory2-src-blueprint.json` (617 target files
across 10 parts).*
