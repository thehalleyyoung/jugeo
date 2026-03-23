"""JuGeo Proof DSL — real proofs via the full sheaf-theoretic pipeline.

Every ``@theorem`` runs the **real** JuGeo pipeline on **real** Python code:

    1. ``jugeo encode``  → constructs Grothendieck site (coordinates, morphisms)
    2. ``jugeo prove``   → generates propositions, checks locally, runs descent
    3.  If H¹ = 0        → descent succeeds, certificate issued
    4.  If H¹ ≠ 0        → obstruction reported with cohomology class

Nothing is string-matched or pattern-faked.  ``ProofResult`` exposes the real
site (coordinate count, morphism count), real propositions, real trust level,
real descent status, and the real certificate hash.

For library functions whose source is unavailable, define *contracts*::

    @library_contract("torch.matmul")
    @requires("A.shape[-1] == B.shape[-2]")
    @ensures("result.shape == (*A.shape[:-1], B.shape[-1])")
    def matmul_contract(A, B): ...

For proof-carrying code, use ``carry_proof``::

    source, certificate = carry_proof(MY_CODE)
    assert certificate.reverify(source)   # O(1) hash check
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')


# ═══════════════════════════════════════════════════════════════════
#  Result types — wrappers around the REAL jugeo output
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SiteInfo:
    """Real site structure produced by ``jugeo prove``."""
    n_coordinates: int
    n_morphisms: int
    category_axioms_pass: bool
    grothendieck_axioms_pass: bool
    grothendieck_detail: Dict[str, bool] = field(default_factory=dict)
    trust_algebra_pass: bool = False
    trust_algebra_axioms: Dict[str, bool] = field(default_factory=dict)


@dataclass
class DescentInfo:
    """Real descent result."""
    H1: str
    effective_descent_verified: int = 0
    effective_descent_total: int = 0
    all_effective: bool = False
    trivial: bool = False


@dataclass
class ProofResult:
    """The REAL result of running ``jugeo prove`` on actual Python code.

    Every field comes from the actual JuGeo pipeline — site construction,
    proposition generation, descent, certificate emission.  Nothing is faked.
    """
    verdict: str
    trust: str
    n_coordinates: int
    n_morphisms: int
    propositions_total: int
    propositions_ok: int
    obstructions: List[Any]
    H1: str
    certificate_hash: str
    site: SiteInfo
    descent: DescentInfo
    elapsed_s: float
    raw: List[Dict[str, Any]]

    @property
    def verified(self) -> bool:
        return self.verdict == "verified"

    @property
    def descent_ok(self) -> bool:
        return self.H1 == "0"

    @property
    def all_axioms_pass(self) -> bool:
        return (self.site.category_axioms_pass
                and self.site.grothendieck_axioms_pass
                and self.site.trust_algebra_pass)


@dataclass
class EquivResult:
    """Result of ``jugeo equiv`` — real sheaf-morphism comparison."""
    verdict: str
    site_a_coords: int
    site_b_coords: int
    refinement_morphisms: int
    obstructions: List[Dict[str, Any]]
    descent_status: str
    raw: List[Dict[str, Any]]

    @property
    def equivalent(self) -> bool:
        return len(self.obstructions) == 0


@dataclass
class BugsResult:
    """Result of ``jugeo bugs`` — real bug detection."""
    bugs: List[Dict[str, Any]]
    count: int
    obstruction_count: int
    raw: List[Dict[str, Any]]


@dataclass
class EncodeResult:
    """Result of ``jugeo encode`` — real site encoding."""
    coordinates: Dict[str, Any]
    judgments: List[Dict[str, Any]]
    n_coordinates: int
    n_declarations: int
    n_assertions: int
    decidability_map: Dict[str, List[str]]
    raw: List[Dict[str, Any]]


# ═══════════════════════════════════════════════════════════════════
#  CLI runner — calls the REAL jugeo binary
# ═══════════════════════════════════════════════════════════════════

def _run_jugeo(*args: str) -> List[Dict[str, Any]]:
    """Run the jugeo CLI and parse the JSON output."""
    cmd = [sys.executable, "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=_ROOT)
    lines = [
        l for l in result.stdout.splitlines()
        if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
        and not l.startswith("JuGeo v")
    ]
    text = "\n".join(lines)
    objects: List[Dict[str, Any]] = []
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


_TEMP_FILES: List[str] = []


def _write_temp(source: str) -> str:
    f = tempfile.NamedTemporaryFile(
        suffix='.py', mode='w', delete=False, dir='/tmp',
    )
    f.write(source)
    f.close()
    _TEMP_FILES.append(f.name)
    return f.name


# ═══════════════════════════════════════════════════════════════════
#  Core: verify / encode / bugs / equiv — wrappers over real CLI
# ═══════════════════════════════════════════════════════════════════

def verify(source: str, *, strategy: str = "eager",
           trust_floor: str = "copilot") -> ProofResult:
    """Run the **real** JuGeo pipeline on Python source code.

    Internally calls ``jugeo prove`` which:
    1. Parses the AST and builds a Grothendieck site
    2. Creates judgments with propositions at each coordinate
    3. Checks propositions locally (type-correct, well-scoped, assertion-safe)
    4. Runs the DescentEngine to glue local sections
    5. Computes H^1 — 0 means success
    6. Issues a proof-carrying certificate
    """
    path = _write_temp(source)
    args = ["prove", path, "--strategy", strategy,
            "--trust-floor", trust_floor]
    objs = _run_jugeo(*args)
    if not objs:
        raise RuntimeError("jugeo prove returned no output")
    prove = objs[0]
    formal = objs[1] if len(objs) >= 2 else {}
    fi = prove.get("files", [{}])[0]
    fv = formal.get("formal_verification", {})
    dl = formal.get("descent_locality", {})
    cat = fv.get("category_structure", {})
    grot = fv.get("grothendieck_axioms", {})
    ta = fv.get("trust_algebra", {})
    ed = dl.get("effective_descent", {})
    of_ = dl.get("obstruction_field", {})
    site = SiteInfo(
        n_coordinates=cat.get("n_objects", fi.get("coordinates", 0)),
        n_morphisms=cat.get("n_morphisms", 0),
        category_axioms_pass=cat.get("axioms", {}).get("all_pass", False),
        grothendieck_axioms_pass=grot.get("all_pass", False),
        grothendieck_detail=grot.get("detail", {}),
        trust_algebra_pass=ta.get("passed", False),
        trust_algebra_axioms=ta.get("axiom_results", {}),
    )
    descent = DescentInfo(
        H1=of_.get("H1", str(fi.get("obstructions", []) or "0")),
        effective_descent_verified=ed.get("verified", 0),
        effective_descent_total=ed.get("total", 0),
        all_effective=ed.get("all_effective", False),
        trivial=of_.get("trivial", False),
    )
    return ProofResult(
        verdict=fi.get("verdict", "unknown"),
        trust=fi.get("trust", "UNVERIFIED"),
        n_coordinates=fi.get("coordinates", 0),
        n_morphisms=site.n_morphisms,
        propositions_total=fi.get("propositions_total", 0),
        propositions_ok=fi.get("propositions_ok", 0),
        obstructions=fi.get("obstructions", []),
        H1=descent.H1,
        certificate_hash=fi.get("certificate", {}).get("hash", ""),
        site=site,
        descent=descent,
        elapsed_s=prove.get("elapsed_s", 0),
        raw=objs,
    )


def encode(source: str) -> EncodeResult:
    """Run ``jugeo encode`` — returns the real site structure."""
    path = _write_temp(source)
    objs = _run_jugeo("encode", path)
    if not objs:
        return EncodeResult({}, [], 0, 0, 0, {}, [])
    enc = objs[0]
    files = enc.get("files", [{}])
    finfo = files[0] if files else {}
    coords = finfo.get("coordinates", {})
    totals = enc.get("totals", {})
    return EncodeResult(
        coordinates=coords,
        judgments=finfo.get("judgments", []),
        n_coordinates=totals.get("coordinates", len(coords)),
        n_declarations=totals.get("declarations", 0),
        n_assertions=totals.get("assertions", 0),
        decidability_map=finfo.get("decidability_map", {}),
        raw=objs,
    )


def find_bugs(source: str) -> BugsResult:
    """Run ``jugeo bugs`` — real bug detection via obstruction analysis."""
    path = _write_temp(source)
    objs = _run_jugeo("bugs", path)
    if not objs:
        return BugsResult([], 0, 0, [])
    entry = objs[0]
    if isinstance(entry, list):
        entry = entry[0] if entry else {}
    return BugsResult(
        bugs=entry.get("bugs", []),
        count=entry.get("count", 0),
        obstruction_count=entry.get("obstruction_count", 0),
        raw=objs,
    )


def check_equiv(source_a: str, source_b: str) -> EquivResult:
    """Run ``jugeo equiv`` — real sheaf-morphism equivalence check."""
    pa = _write_temp(source_a)
    pb = _write_temp(source_b)
    objs = _run_jugeo("equiv", pa, pb)
    if not objs:
        return EquivResult("error", 0, 0, 0, [], "error", [])
    eq = objs[0]
    sa = eq.get("site_a", {})
    sb = eq.get("site_b", {})
    return EquivResult(
        verdict=eq.get("verdict", "unknown"),
        site_a_coords=sa.get("coordinates", 0),
        site_b_coords=sb.get("coordinates", 0),
        refinement_morphisms=eq.get("refinement_morphisms", 0),
        obstructions=eq.get("obstructions", []),
        descent_status=eq.get("descent", {}).get("status", "unknown"),
        raw=objs,
    )


def descend_code(source: str) -> List[Dict[str, Any]]:
    """Run ``jugeo descend`` on source code."""
    path = _write_temp(source)
    return _run_jugeo("descend", path)


# ═══════════════════════════════════════════════════════════════════
#  Library contracts
# ═══════════════════════════════════════════════════════════════════

@dataclass
class LibraryContract:
    """Pre/post-condition contract for a library function."""
    qualified_name: str
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    description: str = ""


_CONTRACT_REGISTRY: Dict[str, LibraryContract] = {}


def requires(condition: str):
    """Decorator: add a precondition."""
    def decorator(fn):
        if not hasattr(fn, '_jugeo_pre'):
            fn._jugeo_pre = []
        fn._jugeo_pre.append(condition)
        return fn
    return decorator


def ensures(condition: str):
    """Decorator: add a postcondition."""
    def decorator(fn):
        if not hasattr(fn, '_jugeo_post'):
            fn._jugeo_post = []
        fn._jugeo_post.append(condition)
        return fn
    return decorator


def library_contract(qualified_name: str, description: str = ""):
    """Register contracts for an external library function."""
    def decorator(fn):
        lc = LibraryContract(
            qualified_name=qualified_name,
            preconditions=getattr(fn, '_jugeo_pre', []),
            postconditions=getattr(fn, '_jugeo_post', []),
            description=description or fn.__doc__ or "",
        )
        _CONTRACT_REGISTRY[qualified_name] = lc
        fn._jugeo_contract = lc
        return fn
    return decorator


def get_contracts() -> Dict[str, LibraryContract]:
    return dict(_CONTRACT_REGISTRY)


# ═══════════════════════════════════════════════════════════════════
#  Proof-carrying code
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ProofCertificate:
    """Certificate bundled with verified code (proof-carrying code)."""
    code_hash: str
    verdict: str
    trust: str
    n_coordinates: int
    n_morphisms: int
    propositions_ok: int
    propositions_total: int
    H1: str
    grothendieck_ok: bool
    trust_algebra_ok: bool
    descent_verified: int
    descent_total: int
    timestamp: float
    contracts_used: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2)

    @classmethod
    def from_json(cls, s: str) -> "ProofCertificate":
        return cls(**json.loads(s))

    def reverify(self, source: str) -> bool:
        """O(1) re-verification: check code hash + verdict."""
        h = hashlib.sha256(source.encode()).hexdigest()[:16]
        return h == self.code_hash and self.verdict == "verified"


def carry_proof(source: str, **kwargs) -> tuple:
    """Verify code and return ``(source, certificate)`` — proof-carrying code."""
    result = verify(source, **kwargs)
    cert = ProofCertificate(
        code_hash=result.certificate_hash or hashlib.sha256(
            source.encode()).hexdigest()[:16],
        verdict=result.verdict,
        trust=result.trust,
        n_coordinates=result.n_coordinates,
        n_morphisms=result.n_morphisms,
        propositions_ok=result.propositions_ok,
        propositions_total=result.propositions_total,
        H1=result.H1,
        grothendieck_ok=result.site.grothendieck_axioms_pass,
        trust_algebra_ok=result.site.trust_algebra_pass,
        descent_verified=result.descent.effective_descent_verified,
        descent_total=result.descent.effective_descent_total,
        timestamp=time.time(),
        contracts_used=list(_CONTRACT_REGISTRY.keys()),
    )
    return source, cert


# ═══════════════════════════════════════════════════════════════════
#  @theorem / @check decorators
# ═══════════════════════════════════════════════════════════════════

_REGISTRY: List[tuple] = []


def reset():
    """Clear the theorem/check registry."""
    _REGISTRY.clear()


def theorem(name: str, *, code: Optional[str] = None):
    """Decorator: define a theorem proved by the real JuGeo pipeline.

    If *code* is given, ``jugeo prove`` is run on that code and the
    ``ProofResult`` is passed to the body.  The body asserts properties
    of the **real** result (coordinates, propositions, H^1, axioms).

    If *code* is omitted, the body calls ``verify()`` / ``encode()`` /
    ``find_bugs()`` / ``check_equiv()`` explicitly.

    Usage::

        @theorem("Attention is shape-safe", code=ATTN_CODE)
        def attention_proof(result):
            assert result.verified
            assert result.H1 == "0"
            assert result.site.grothendieck_axioms_pass
    """
    def decorator(fn: Callable) -> Callable:
        fn._theorem_name = name

        def wrapper():
            if code is not None:
                result = verify(code)
                fn(result)
                return result
            else:
                return fn()

        wrapper.__name__ = fn.__name__
        wrapper._theorem_name = name
        _REGISTRY.append(("theorem", wrapper))
        return wrapper

    return decorator


def check(description: str):
    """Decorator: register an empirical verification check."""
    def decorator(fn: Callable) -> Callable:
        fn._check_description = description

        def wrapper() -> tuple:
            try:
                fn()
                return True, ""
            except Exception as exc:
                return False, str(exc)

        wrapper.__name__ = fn.__name__
        wrapper._check_description = description
        _REGISTRY.append(("check", wrapper))
        return wrapper

    return decorator


# ═══════════════════════════════════════════════════════════════════
#  Runner
# ═══════════════════════════════════════════════════════════════════

def run_all(title: str = "JuGeo Proofs") -> bool:
    """Run every registered @theorem and @check, print results, exit."""
    print(f"\n{title}")
    print("=" * 60)

    theorems_ok = 0
    theorems_fail = 0
    checks_ok = 0
    checks_fail = 0

    for kind, fn in _REGISTRY:
        if kind == "theorem":
            tname = fn._theorem_name
            try:
                result = fn()
                theorems_ok += 1
                if isinstance(result, ProofResult):
                    print(f"\n  Theorem: {tname}")
                    print(f"    site    {result.n_coordinates} coords, "
                          f"{result.n_morphisms} morphisms")
                    print(f"    props   {result.propositions_ok}/"
                          f"{result.propositions_total} verified")
                    print(f"    descent H\u00b9 = {result.H1}")
                    print(f"    trust   {result.trust}")
                    print(f"    \u2713 QED \u2014 {result.verdict} "
                          f"({result.elapsed_s:.2f}s)")
                else:
                    print(f"  \u2713 Theorem: {tname}")
            except Exception as exc:
                theorems_fail += 1
                print(f"  \u2717 Theorem: {tname} \u2014 {exc}")

        elif kind == "check":
            desc = fn._check_description
            ok, err = fn()
            if ok:
                checks_ok += 1
                print(f"  \u2713 Check: {desc}")
            else:
                checks_fail += 1
                print(f"  \u2717 Check: {desc} \u2014 {err}")

    total_ok = theorems_ok + checks_ok
    total_fail = theorems_fail + checks_fail
    total = total_ok + total_fail

    print(f"\n{'\u2500' * 60}")
    print(
        f"  {theorems_ok} theorems | {checks_ok} checks"
        f" | {total} total | {total_fail} failures"
    )
    print("=" * 60)

    for f in _TEMP_FILES:
        try:
            os.unlink(f)
        except OSError:
            pass
    _TEMP_FILES.clear()

    if total_fail:
        sys.exit(1)
    return True
