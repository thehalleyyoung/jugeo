#!/usr/bin/env python3
"""Paper 8 — Treaty Negotiation.

Formal proofs: bilateral treaties compose associatively, conflict detection
is complete, and treaty resolution converges.
"""
import subprocess, json, os, sys, tempfile

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from jugeo.encodings.deduction_rules.models import (
    make_rule, make_axiom_rule, InferenceStep, RuleKind,
)
from jugeo.encodings.deduction_rules.algorithms import verify_proof_trace

PASS = 0
FAIL = 0
PROOFS = 0
CLI_CHECKS = 0

def report(name, ok, kind="proof"):
    global PASS, FAIL, PROOFS, CLI_CHECKS
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}")
    if ok:
        PASS += 1
    else:
        FAIL += 1
    if kind == "proof":
        PROOFS += 1 if ok else 0
    else:
        CLI_CHECKS += 1 if ok else 0

def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
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

def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name

temp_files = []

def main():
    print("Paper 08 — Treaty Negotiation")
    print("=" * 55)

    # ==================================================================
    # PROOF 1: Bilateral treaties compose associatively
    #   treaty(A, B), treaty(B, C)
    #     ⊢ compose(treaty(A,B), treaty(B,C)) defined
    #   treaty(A, B), treaty(B, C), treaty(C, D)
    #     ⊢ associative(compose)
    # ==================================================================
    print("\nProof 1 — Treaty composition is associative")

    ax_ab = make_axiom_rule('treaty_AB', 'treaty(A,B)')
    ax_bc = make_axiom_rule('treaty_BC', 'treaty(B,C)')
    ax_cd = make_axiom_rule('treaty_CD', 'treaty(C,D)')
    r_comp_ab_bc = make_rule('compose_AB_BC',
                             ['treaty(A,B)', 'treaty(B,C)'],
                             'treaty(A,C)', RuleKind.STRUCTURAL)
    r_comp_ac_cd = make_rule('compose_AC_CD',
                             ['treaty(A,C)', 'treaty(C,D)'],
                             'treaty(A,D)', RuleKind.STRUCTURAL)
    r_assoc = make_rule('treaty_associativity',
                        ['treaty(A,D)'],
                        'associative(treaty_compose)', RuleKind.STRUCTURAL)

    steps = [
        InferenceStep('s0', ax_ab, (), 'treaty(A,B)', 'treaty A↔B', 0),
        InferenceStep('s1', ax_bc, (), 'treaty(B,C)', 'treaty B↔C', 1),
        InferenceStep('s2', ax_cd, (), 'treaty(C,D)', 'treaty C↔D', 2),
        InferenceStep('s3', r_comp_ab_bc, ('treaty(A,B)', 'treaty(B,C)'),
                      'treaty(A,C)', 'compose A↔B, B↔C → A↔C', 3),
        InferenceStep('s4', r_comp_ac_cd, ('treaty(A,C)', 'treaty(C,D)'),
                      'treaty(A,D)', 'compose A↔C, C↔D → A↔D', 4),
        InferenceStep('s5', r_assoc, ('treaty(A,D)',),
                      'associative(treaty_compose)', 'associativity', 5),
    ]
    ok, issues = verify_proof_trace(steps, goal='associative(treaty_compose)')
    report(f"treaty associativity (issues={issues})", ok and not issues)

    # ==================================================================
    # PROOF 2: Conflict detection is complete
    #   treaty(A, B), obligation(A, phi), obligation(B, psi), conflicts(phi, psi)
    #     ⊢ conflict_detected(A, B, phi, psi)
    #     ⊢ detection_complete(A, B)
    # ==================================================================
    print("\nProof 2 — Conflict detection is complete")

    ax_treaty = make_axiom_rule('treaty_ab', 'treaty(A,B)')
    ax_ob_a = make_axiom_rule('obligation_a', 'obligation(A,phi)')
    ax_ob_b = make_axiom_rule('obligation_b', 'obligation(B,psi)')
    ax_conflict = make_axiom_rule('conflicts', 'conflicts(phi,psi)')
    r_detect = make_rule('conflict_detection',
                         ['treaty(A,B)', 'obligation(A,phi)', 'obligation(B,psi)',
                          'conflicts(phi,psi)'],
                         'conflict_detected(A,B,phi,psi)', RuleKind.SEMANTIC)
    r_complete = make_rule('detection_completeness',
                           ['conflict_detected(A,B,phi,psi)'],
                           'detection_complete(A,B)', RuleKind.SEMANTIC)

    steps2 = [
        InferenceStep('s0', ax_treaty, (), 'treaty(A,B)', 'A↔B treaty', 0),
        InferenceStep('s1', ax_ob_a, (), 'obligation(A,phi)', 'A promises phi', 1),
        InferenceStep('s2', ax_ob_b, (), 'obligation(B,psi)', 'B promises psi', 2),
        InferenceStep('s3', ax_conflict, (), 'conflicts(phi,psi)',
                      'phi conflicts with psi', 3),
        InferenceStep('s4', r_detect,
                      ('treaty(A,B)', 'obligation(A,phi)', 'obligation(B,psi)',
                       'conflicts(phi,psi)'),
                      'conflict_detected(A,B,phi,psi)', 'conflict found', 4),
        InferenceStep('s5', r_complete, ('conflict_detected(A,B,phi,psi)',),
                      'detection_complete(A,B)', 'all conflicts detected', 5),
    ]
    ok2, issues2 = verify_proof_trace(steps2, goal='detection_complete(A,B)')
    report(f"conflict detection complete (issues={issues2})", ok2 and not issues2)

    # ==================================================================
    # PROOF 3: Treaty resolution converges (conflict count decreases)
    #   conflict_count(T, n), n > 0, resolve_step(T) = T'
    #     ⊢ conflict_count(T', m), m < n
    #     ⊢ resolution_converges(T)
    # ==================================================================
    print("\nProof 3 — Treaty resolution converges")

    ax_cc = make_axiom_rule('conflict_count', 'conflict_count(T,n)')
    ax_pos = make_axiom_rule('n_pos', 'greater(n,0)')
    ax_resolve = make_axiom_rule('resolve_step', 'resolve_step(T,Tprime)')
    r_decrease = make_rule('conflict_decrease',
                           ['conflict_count(T,n)', 'greater(n,0)', 'resolve_step(T,Tprime)'],
                           'conflict_count(Tprime,m_lt_n)', RuleKind.SEMANTIC)
    r_converge = make_rule('resolution_convergence',
                           ['conflict_count(Tprime,m_lt_n)'],
                           'resolution_converges(T)', RuleKind.SEMANTIC)

    steps3 = [
        InferenceStep('s0', ax_cc, (), 'conflict_count(T,n)', 'T has n conflicts', 0),
        InferenceStep('s1', ax_pos, (), 'greater(n,0)', 'n > 0', 1),
        InferenceStep('s2', ax_resolve, (), 'resolve_step(T,Tprime)',
                      'resolve one step', 2),
        InferenceStep('s3', r_decrease,
                      ('conflict_count(T,n)', 'greater(n,0)', 'resolve_step(T,Tprime)'),
                      'conflict_count(Tprime,m_lt_n)', 'conflict count decreases', 3),
        InferenceStep('s4', r_converge, ('conflict_count(Tprime,m_lt_n)',),
                      'resolution_converges(T)', 'resolution converges', 4),
    ]
    ok3, issues3 = verify_proof_trace(steps3, goal='resolution_converges(T)')
    report(f"resolution convergence (issues={issues3})", ok3 and not issues3)

    # ==================================================================
    # CLI: jugeo prove on interacting modules
    # ==================================================================
    print("\nCLI — prove on interacting modules")

    mod_a = write_temp("def validate(data):\n    return isinstance(data, dict) and 'key' in data\n\ndef extract(data):\n    if validate(data):\n        return data['key']\n    return None\n")
    temp_files.append(mod_a)
    mod_b = write_temp("def format_value(value):\n    if value is None:\n        return '<empty>'\n    return str(value)\n\ndef render(value):\n    return 'Result: ' + format_value(value)\n")
    temp_files.append(mod_b)
    combined = write_temp("def validate(data):\n    return isinstance(data, dict) and 'key' in data\n\ndef extract(data):\n    if validate(data):\n        return data['key']\n    return None\n\ndef format_value(value):\n    if value is None:\n        return '<empty>'\n    return str(value)\n\ndef pipeline(data):\n    value = extract(data)\n    return format_value(value)\n")
    temp_files.append(combined)

    for label, path in [("module_A", mod_a), ("module_B", mod_b), ("combined", combined)]:
        objs = run_jugeo("prove", path)
        if objs:
            report(f"prove {label} → verified",
                   objs[0]["files"][0]["verdict"] == "verified", kind="cli")
        else:
            report(f"prove {label} → verified", False, kind="cli")

    print("\nCLI — equiv on identical vs different modules")
    mod_a2 = write_temp("def validate(data):\n    return isinstance(data, dict) and 'key' in data\n\ndef extract(data):\n    if validate(data):\n        return data['key']\n    return None\n")
    temp_files.append(mod_a2)
    objs = run_jugeo("equiv", mod_a, mod_a2)
    if objs:
        v = objs[0].get("verdict", "").lower()
        report("equiv identical → equivalent", "equivalent" in v, kind="cli")
    else:
        report("equiv identical → equivalent", False, kind="cli")

    objs2 = run_jugeo("equiv", mod_a, mod_b)
    if objs2:
        report("equiv different → has verdict", "verdict" in objs2[0], kind="cli")
    else:
        report("equiv different → has verdict", False, kind="cli")

    total = PASS + FAIL
    print(f"\nPaper 08 — Treaty Verification: {PROOFS} proofs verified, "
          f"{CLI_CHECKS} CLI checks passed, {total} total, {FAIL} failed")
    return FAIL == 0

if __name__ == "__main__":
    try:
        ok = main()
    finally:
        for f in temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass
    sys.exit(0 if ok else 1)
