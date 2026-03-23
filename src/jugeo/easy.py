"""jugeo.easy — One-line access to everything JuGeo can do.

This module is the "bang for your intellectual buck" entry point.
Every function takes a string of Python code and returns a rich result
object. No sites, no coordinates, no morphisms — just results.

    >>> from jugeo.easy import prove, bugs, spec, equiv, ideate
    >>>
    >>> # Verify any Python code — one function call
    >>> result = prove(open("mycode.py").read())
    >>> print(result.verdict, result.trust, result.H1)
    >>>
    >>> # Find bugs — one function call
    >>> bugs_found = bugs(open("mycode.py").read())
    >>> for b in bugs_found:
    ...     print(b.kind, b.line, b.message)
    >>>
    >>> # Check code against an executable spec on a declared cover
    >>> review = spec(
    ...     "def solve(n): return n * 2",
    ...     "def spec(result, n): return result == n * 2",
    ...     input_cover=[{"args": [n], "kwargs": {}} for n in range(10)],
    ... )
    >>> print(review.satisfied, review.witness_count)
    >>>
    >>> # Check two programs are equivalent
    >>> eq = equiv(prog_a, prog_b)
    >>> print("Equivalent!" if eq.equivalent else eq.counterexample)
    >>>
    >>> # Ideate a new mathematical domain
    >>> ideas = ideate("Algebraic topology for distributed systems")
    >>> for thm in ideas.theorems:
    ...     print(thm.statement, thm.novelty_score)

Under the hood, each function constructs a Grothendieck site, runs the
full descent engine, and returns structured results with trust levels
and cohomological diagnostics. But you don't need to know any of that.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import os
from dataclasses import dataclass
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════
#  Internal: run jugeo CLI
# ═══════════════════════════════════════════════════════════════════

def _run(cmd: list[str]) -> list[dict]:
    full = [sys.executable, "-m", "jugeo", "--format", "json"] + cmd
    result = subprocess.run(full, capture_output=True, text=True, cwd=_ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects: list[dict] = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = decoder.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects


def _tmp(source: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
    f.write(source)
    f.close()
    return f.name


# ═══════════════════════════════════════════════════════════════════
#  Result types — simple, friendly dataclasses
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ProveResult:
    """Result of verifying Python code.

    Key attributes:
        verdict: "verified" or "obstructed"
        trust: Trust level (e.g. "SOLVER_DISCHARGED")
        H1: "0" means all proofs passed (descent succeeded)
        coordinates: Number of program coordinates in the site
        propositions: (ok, total) tuple
        certificate_hash: Hash for O(1) re-verification
        obstructions: List of structured proof failures
    """
    verdict: str
    trust: str
    H1: str
    coordinates: int
    propositions: tuple[int, int]
    certificate_hash: str
    obstructions: list[dict]
    grothendieck_ok: bool
    trust_algebra_ok: bool
    elapsed: float
    raw: list[dict]

    @property
    def verified(self) -> bool:
        return self.verdict == "verified"

    @property
    def descent_ok(self) -> bool:
        return self.H1 == "0"

    def __repr__(self):
        status = "VERIFIED" if self.verified else "OBSTRUCTED"
        return (f"ProveResult({status}, trust={self.trust}, "
                f"H1={self.H1}, coords={self.coordinates}, "
                f"props={self.propositions[0]}/{self.propositions[1]})")


@dataclass
class Bug:
    """A single detected bug."""
    kind: str
    line: int
    message: str
    severity: str
    fix_hint: str
    coordinate: str
    raw: dict

    def __repr__(self):
        return f"Bug({self.kind}, line {self.line}: {self.message})"


@dataclass
class BugsResult:
    """Result of bug detection."""
    items: list[Bug]
    count: int
    raw: list[dict]

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return self.count

    def __repr__(self):
        return f"BugsResult({self.count} bugs found)"


@dataclass
class EquivResult:
    """Result of equivalence checking."""
    equivalent: bool
    verdict: str
    counterexample: Optional[str]
    coordinates_a: int
    coordinates_b: int
    obstructions: list[dict]
    raw: list[dict]

    def __repr__(self):
        if self.equivalent:
            return "EquivResult(EQUIVALENT)"
        return f"EquivResult(NOT EQUIVALENT, {len(self.obstructions)} obstructions)"


@dataclass
class SpecResult:
    """Result of checking a program against an executable specification."""
    satisfied: bool
    trust_level: float
    clauses: list[dict]
    witnesses: list[dict]
    obstructions: list[str]
    residual_obligations: list[str]
    mode: str
    raw: list[dict]

    @property
    def witness_count(self) -> int:
        return len(self.witnesses) if self.witnesses else len(self.obstructions)

    def __repr__(self):
        status = "SATISFIED" if self.satisfied else "VIOLATED"
        return (
            f"SpecResult({status}, trust={self.trust_level:.2f}, "
            f"clauses={len(self.clauses)}, witnesses={self.witness_count})"
        )


@dataclass
class Theorem:
    """A discovered theorem from ideation."""
    statement: str
    kind: str
    novelty_score: float
    confidence: float
    domain: str
    proof_sketch: str
    raw: dict

    def __repr__(self):
        return f"Theorem({self.kind}: {self.statement[:60]}..., novelty={self.novelty_score:.2f})"


@dataclass
class IdeateResult:
    """Result of mathematical ideation."""
    domain: str
    theorems: list[Theorem]
    conjectures: list[dict]
    definitions: list[dict]
    connections: list[dict]
    raw: list[dict]

    def __repr__(self):
        return (f"IdeateResult(domain={self.domain!r}, "
                f"{len(self.theorems)} theorems, "
                f"{len(self.conjectures)} conjectures)")


# ═══════════════════════════════════════════════════════════════════
#  Public API — one function per capability
# ═══════════════════════════════════════════════════════════════════

def prove(source: str, *, strategy: str = "eager") -> ProveResult:
    """Verify Python code. Returns structured proof result.

    Example::

        from jugeo.easy import prove

        result = prove('''
        def factorial(n):
            if n <= 1:
                return 1
            return n * factorial(n - 1)
        ''')
        print(result)  # ProveResult(VERIFIED, trust=SOLVER_DISCHARGED, ...)
    """
    path = _tmp(source)
    objs = _run(["prove", path, "--strategy", strategy])
    if not objs:
        return ProveResult("error", "UNVERIFIED", "?", 0, (0, 0), "", [], False, False, 0, [])
    p = objs[0]
    fi = p.get("files", [{}])[0]
    fv = objs[1] if len(objs) >= 2 else {}
    formal = fv.get("formal_verification", {})
    grot = formal.get("grothendieck_axioms", {})
    ta = formal.get("trust_algebra", {})
    dl = fv.get("descent_locality", {})
    of_ = dl.get("obstruction_field", {})
    return ProveResult(
        verdict=fi.get("verdict", "unknown"),
        trust=fi.get("trust", "UNVERIFIED"),
        H1=of_.get("H1", str(fi.get("obstructions", []) or "0")),
        coordinates=fi.get("coordinates", 0),
        propositions=(fi.get("propositions_ok", 0), fi.get("propositions_total", 0)),
        certificate_hash=fi.get("certificate", {}).get("hash", ""),
        obstructions=fi.get("obstructions", []),
        grothendieck_ok=grot.get("all_pass", False),
        trust_algebra_ok=ta.get("passed", False),
        elapsed=p.get("elapsed_s", 0),
        raw=objs,
    )


def bugs(source: str) -> BugsResult:
    """Find bugs in Python code. Returns structured bug list.

    Detects 6 bug classes as cohomological obstructions:
    - bare-except: empty except clauses
    - identity-literal: `x is 1` instead of `x == 1`
    - late-binding-closure: closure variable capture bugs
    - mutable-default: `def f(x=[])` shared mutation
    - open-without-close: unclosed file handles
    - shadow-builtin: `list = [1,2,3]` shadowing builtins

    Example::

        from jugeo.easy import bugs

        result = bugs('''
        def process(data=[]):   # mutable default!
            data.append(1)
            try:
                return data
            except:             # bare except!
                pass
        ''')
        for bug in result:
            print(bug)
    """
    path = _tmp(source)
    objs = _run(["bugs", path])
    if not objs:
        return BugsResult([], 0, [])
    entry = objs[0]
    if isinstance(entry, list):
        entry = entry[0] if entry else {}
    raw_bugs = entry.get("bugs", [])
    items = []
    for b in raw_bugs:
        items.append(Bug(
            kind=b.get("kind", b.get("label", "unknown")),
            line=b.get("line", 0),
            message=b.get("message", b.get("description", "")),
            severity=b.get("severity", "warning"),
            fix_hint=b.get("fix_hint", b.get("fix", "")),
            coordinate=b.get("coordinate", ""),
            raw=b,
        ))
    return BugsResult(items=items, count=len(items), raw=objs)


def equiv(source_a: str, source_b: str) -> EquivResult:
    """Check if two Python programs are semantically equivalent.

    Example::

        from jugeo.easy import equiv

        result = equiv(
            "def f(xs): return sorted(set(xs))",
            "def f(xs): return list(dict.fromkeys(sorted(xs)))",
        )
        print(result)  # EquivResult(EQUIVALENT) or EquivResult(NOT EQUIVALENT, ...)
    """
    pa, pb = _tmp(source_a), _tmp(source_b)
    objs = _run(["equiv", pa, pb])
    if not objs:
        return EquivResult(False, "error", None, 0, 0, [], [])
    eq = objs[0]
    obs = eq.get("obstructions", [])
    ce = None
    if obs:
        ce = obs[0].get("counterexample", obs[0].get("description", str(obs[0])))
    return EquivResult(
        equivalent=len(obs) == 0,
        verdict=eq.get("verdict", "unknown"),
        counterexample=ce,
        coordinates_a=eq.get("site_a", {}).get("coordinates", 0),
        coordinates_b=eq.get("site_b", {}).get("coordinates", 0),
        obstructions=obs,
        raw=objs,
    )


def _spec_direct(source: str, payload: dict) -> SpecResult:
    """Run spec check in-process using benchmarks.semantics primitives.

    This bypasses the CLI subprocess and directly executes the spec function
    against each cover point, matching the logic in cmd_spec._run_runtime_cover_pipeline.
    """
    from jugeo.benchmarks.models import InputPoint
    from jugeo.benchmarks.semantics import call_fresh, load_function

    spec_program: str = payload["spec_program"]
    input_cover: list = payload["input_cover"]
    fn_name: str = payload.get("entrypoint", "solve")
    spec_fn_name: str = payload.get("spec_function", "spec")

    points = tuple(InputPoint.from_dict(item) for item in input_cover)
    spec_fn = load_function(spec_program, spec_fn_name)

    clauses: list[dict] = []
    obstructions: list[str] = []
    witnesses: list[dict] = []
    residuals: list[str] = []
    satisfied_count = 0

    for index, point in enumerate(points):
        text = f"{fn_name} respects {spec_fn_name} on cover[{index}]"
        outcome = call_fresh(source, fn_name, point)
        passed = False
        detail = ""

        if outcome.tag != "return":
            detail = (
                f"{fn_name} produced {outcome.tag}={outcome.value!r} "
                f"for declared cover[{index}]"
            )
        else:
            try:
                spec_result = spec_fn(outcome.value, *point.args, **point.kwargs)
                if isinstance(spec_result, bool):
                    passed = spec_result
                    if passed:
                        detail = (
                            f"result={outcome.value!r} accepted on cover[{index}]"
                        )
                    else:
                        detail = (
                            f"{spec_fn_name} returned False for "
                            f"result={outcome.value!r} on cover[{index}]"
                        )
                else:
                    detail = (
                        f"{spec_fn_name} returned {type(spec_result).__name__}, "
                        f"not bool, on cover[{index}]"
                    )
            except Exception as exc:
                detail = (
                    f"{spec_fn_name} raised {type(exc).__name__}: {exc} "
                    f"on cover[{index}]"
                )

        if passed:
            satisfied_count += 1
        else:
            obstructions.append(f"cover[{index}]: {detail}")
            residuals.append(f"obligation at cover[{index}]: {detail}")
            witnesses.append({
                "cover_index": index,
                "input_point": point.to_dict(),
                "outcome": {"tag": outcome.tag, "value": outcome.value},
                "detail": detail,
            })

        clauses.append({
            "text": text,
            "pass": passed,
            "status": "SETTLED" if passed else "FAIL",
            "detail": detail,
            "input_point": point.to_dict(),
        })

    total = len(points) or 1
    satisfied = satisfied_count == total
    trust = round(satisfied_count / total, 4)

    return SpecResult(
        satisfied=satisfied,
        trust_level=trust,
        clauses=clauses,
        witnesses=witnesses,
        obstructions=obstructions,
        residual_obligations=residuals,
        mode="runtime-declared-cover",
        raw=[],
    )


def spec(
    source: str,
    specification: str | dict,
    *,
    input_cover: list[dict] | None = None,
    entrypoint: str = "solve",
    spec_function: str = "spec",
    description: str = "",
) -> SpecResult:
    """Check code against an executable specification on a declared cover.

    ``specification`` may be either a raw ``spec_program`` string defining
    ``spec(result, *args, **kwargs) -> bool`` or a full JSON-ready payload.
    """
    if isinstance(specification, dict):
        payload = dict(specification)
    else:
        payload = {"spec_program": specification}
    if input_cover is not None:
        payload["input_cover"] = input_cover
    payload.setdefault("entrypoint", entrypoint)
    payload.setdefault("spec_function", spec_function)
    if description and "description" not in payload:
        payload["description"] = description

    # Fast path: run directly in-process when spec_program and input_cover
    # are present. This avoids subprocess import failures that cause the
    # runtime cover pipeline to silently fall back to AST-only checking.
    if "spec_program" in payload and "input_cover" in payload:
        try:
            return _spec_direct(source, payload)
        except Exception:
            pass

    source_path = _tmp(source)
    spec_file = tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False)
    json.dump(payload, spec_file)
    spec_file.close()
    try:
        objs = _run(["spec", spec_file.name, source_path])
    finally:
        try:
            os.unlink(source_path)
        except OSError:
            pass
        try:
            os.unlink(spec_file.name)
        except OSError:
            pass

    entry = objs[0] if objs else {}
    if not isinstance(entry, dict):
        entry = {}
    return SpecResult(
        satisfied=bool(entry.get("satisfied", False)),
        trust_level=float(entry.get("trust_level", 0.0) or 0.0),
        clauses=list(entry.get("clauses", [])),
        witnesses=list(entry.get("witnesses", [])),
        obstructions=list(entry.get("obstructions", [])),
        residual_obligations=list(entry.get("residual_obligations", [])),
        mode=str(entry.get("mode", "unknown")),
        raw=objs,
    )


def ideate(topic: str, *, n: int = 5) -> IdeateResult:
    """Ideate new mathematics in a given domain.

    Uses JuGeo's discovery engine to propose theorems, conjectures,
    and definitions in the specified topic area.

    Example::

        from jugeo.easy import ideate

        result = ideate("Homotopy type theory for database migrations")
        for thm in result.theorems:
            print(thm.statement)
            print(f"  Novelty: {thm.novelty_score:.2f}")
    """
    objs = _run(["ideate", "--topic", topic, "--count", str(n)])
    if not objs:
        return IdeateResult(topic, [], [], [], [], [])
    entry = objs[0]
    raw_thms = entry.get("theorems", entry.get("candidates", []))
    theorems = []
    for t in raw_thms:
        theorems.append(Theorem(
            statement=t.get("statement", t.get("name", "")),
            kind=t.get("kind", "theorem"),
            novelty_score=t.get("novelty_score", t.get("novelty", 0.0)),
            confidence=t.get("confidence", 0.0),
            domain=t.get("domain", topic),
            proof_sketch=t.get("proof_sketch", t.get("sketch", "")),
            raw=t,
        ))
    return IdeateResult(
        domain=topic,
        theorems=theorems,
        conjectures=entry.get("conjectures", []),
        definitions=entry.get("definitions", []),
        connections=entry.get("connections", []),
        raw=objs,
    )


def carry(source: str, **kwargs) -> tuple[str, dict]:
    """Verify code and return (source, certificate) for proof-carrying deployment.

    Example::

        source, cert = carry(my_code)
        # cert contains: verdict, trust, H1, coordinates, certificate_hash
        # Ship source + cert together; re-verify with hash check in O(1)
    """
    result = prove(source, **kwargs)
    cert = {
        "verdict": result.verdict,
        "trust": result.trust,
        "H1": result.H1,
        "coordinates": result.coordinates,
        "propositions_ok": result.propositions[0],
        "propositions_total": result.propositions[1],
        "certificate_hash": result.certificate_hash,
        "grothendieck_ok": result.grothendieck_ok,
        "trust_algebra_ok": result.trust_algebra_ok,
    }
    return source, cert


def research(
    prompt: str,
    *,
    max_iterations: int = 30,
    max_pivots: int = 3,
    output_dir: str | None = None,
    no_llm: bool = False,
    seed: int | None = None,
    verbose: bool = False,
):
    """Run a full directed research loop from a natural-language prompt.

    Given a high-level idea like "build a killer app in finance using
    advanced math", this function:

    1. Ideates a novel mathematical approach (cross-tradition synthesis)
    2. Designs a software plan (covering family decomposition)
    3. Generates code (local sections on each module)
    4. Establishes benchmarks and baselines (evidence sections)
    5. Evaluates via workspace descent (consistency check)
    6. Refines or pivots until competitive and consistent
    7. Writes README.md and conference_tool_track.tex/.pdf

    Every step is a semantic move on the workspace site (T, R, E, P).

    Example::

        from jugeo.easy import research

        result = research(
            "Build a Python tool that uses tropical geometry to optimize supply chains",
            max_iterations=20,
        )
        print(result)
        # DirectedResearchResult(
        #   status=converged,
        #   approach='tropical-supply-optimization',
        #   code_files=7,
        #   consistency=ConsistencyReport(CONSISTENT, ...),
        #   iterations=12,
        # )
    """
    from jugeo.directed_research import DirectedResearch

    dr = DirectedResearch(
        prompt=prompt,
        max_iterations=max_iterations,
        max_pivots=max_pivots,
        output_dir=output_dir,
        no_llm=no_llm,
        seed=seed,
        verbose=verbose,
    )
    return dr.run()
