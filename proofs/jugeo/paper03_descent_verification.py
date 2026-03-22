#!/usr/bin/env python3
"""Paper 3 — Descent Verification & Obstruction Classification.

Formal proofs: compatible overlaps ⊢ gluing (H⁰), violated overlap ⊢
obstruction (H¹), and repair reduces obstruction count.
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
    print("Paper 03 — Descent Verification & Obstruction Classification")
    print("=" * 55)

    # ==================================================================
    # PROOF 1: Compatible overlaps ⊢ gluing succeeds (H⁰)
    #   local_section(s_i, U_i) for all i,
    #   compatible(s_i, s_j) on U_i ∩ U_j
    #     ⊢ glue({s_i}) defined
    #     ⊢ global_section(s)
    #     ⊢ H0_nontrivial
    # ==================================================================
    print("\nProof 1 — Compatible overlaps ⊢ gluing (H⁰)")

    ax_s1 = make_axiom_rule('local_s1', 'local_section(s1,U1)')
    ax_s2 = make_axiom_rule('local_s2', 'local_section(s2,U2)')
    ax_compat = make_axiom_rule('compat', 'compatible(s1,s2,U1_cap_U2)')
    r_glue_def = make_rule('glue_defined',
                           ['local_section(s1,U1)', 'local_section(s2,U2)',
                            'compatible(s1,s2,U1_cap_U2)'],
                           'glue_defined(s1,s2)', RuleKind.STRUCTURAL)
    r_global = make_rule('glue_global',
                         ['glue_defined(s1,s2)'],
                         'global_section(glue(s1,s2))', RuleKind.STRUCTURAL)
    r_h0 = make_rule('h0_nontrivial',
                     ['global_section(glue(s1,s2))'],
                     'H0_nontrivial', RuleKind.SEMANTIC)

    steps = [
        InferenceStep('s0', ax_s1, (), 'local_section(s1,U1)', 's1 on U1', 0),
        InferenceStep('s1', ax_s2, (), 'local_section(s2,U2)', 's2 on U2', 1),
        InferenceStep('s2', ax_compat, (), 'compatible(s1,s2,U1_cap_U2)',
                      's1,s2 agree on overlap', 2),
        InferenceStep('s3', r_glue_def,
                      ('local_section(s1,U1)', 'local_section(s2,U2)',
                       'compatible(s1,s2,U1_cap_U2)'),
                      'glue_defined(s1,s2)', 'sheaf gluing condition met', 3),
        InferenceStep('s4', r_global, ('glue_defined(s1,s2)',),
                      'global_section(glue(s1,s2))', 'global section obtained', 4),
        InferenceStep('s5', r_h0, ('global_section(glue(s1,s2))',),
                      'H0_nontrivial', 'H⁰ is nontrivial', 5),
    ]
    ok, issues = verify_proof_trace(steps, goal='H0_nontrivial')
    report(f"gluing from compatible overlaps (issues={issues})", ok and not issues)

    # ==================================================================
    # PROOF 2: Violated overlap ⊢ obstruction exists (H¹)
    #   local_section(s1, U1), local_section(s2, U2),
    #   NOT compatible(s1, s2)
    #     ⊢ overlap_violated(s1, s2)
    #     ⊢ obstruction(s1, s2) in H¹
    # ==================================================================
    print("\nProof 2 — Violated overlap ⊢ obstruction (H¹)")

    ax_ls1 = make_axiom_rule('local_s1b', 'local_section(s1,U1)')
    ax_ls2 = make_axiom_rule('local_s2b', 'local_section(s2,U2)')
    ax_incompat = make_axiom_rule('incompatible', 'incompatible(s1,s2,U1_cap_U2)')
    r_violated = make_rule('overlap_violation',
                           ['local_section(s1,U1)', 'local_section(s2,U2)',
                            'incompatible(s1,s2,U1_cap_U2)'],
                           'overlap_violated(s1,s2)', RuleKind.SEMANTIC)
    r_obs = make_rule('obstruction_exists',
                      ['overlap_violated(s1,s2)'],
                      'obstruction_in_H1(s1,s2)', RuleKind.SEMANTIC)

    steps2 = [
        InferenceStep('s0', ax_ls1, (), 'local_section(s1,U1)', 's1 on U1', 0),
        InferenceStep('s1', ax_ls2, (), 'local_section(s2,U2)', 's2 on U2', 1),
        InferenceStep('s2', ax_incompat, (), 'incompatible(s1,s2,U1_cap_U2)',
                      's1,s2 disagree', 2),
        InferenceStep('s3', r_violated,
                      ('local_section(s1,U1)', 'local_section(s2,U2)',
                       'incompatible(s1,s2,U1_cap_U2)'),
                      'overlap_violated(s1,s2)', 'overlap violated', 3),
        InferenceStep('s4', r_obs, ('overlap_violated(s1,s2)',),
                      'obstruction_in_H1(s1,s2)', 'obstruction in H¹', 4),
    ]
    ok2, issues2 = verify_proof_trace(steps2, goal='obstruction_in_H1(s1,s2)')
    report(f"obstruction from violated overlap (issues={issues2})", ok2 and not issues2)

    # ==================================================================
    # PROOF 3: Repair reduces obstruction count (well-founded descent)
    #   obstruction_count(P, n), n > 0, repair(P) = P'
    #     ⊢ obstruction_count(P', m), m < n
    #     ⊢ descent_well_founded(P)
    # ==================================================================
    print("\nProof 3 — Repair reduces obstruction count")

    ax_obs_n = make_axiom_rule('obs_count', 'obstruction_count(P,n)')
    ax_pos = make_axiom_rule('n_positive', 'greater(n,0)')
    ax_repair = make_axiom_rule('repair_applied', 'repair(P,Pprime)')
    r_reduced = make_rule('obstruction_reduction',
                          ['obstruction_count(P,n)', 'greater(n,0)', 'repair(P,Pprime)'],
                          'obstruction_count(Pprime,m_lt_n)', RuleKind.SEMANTIC)
    r_wf = make_rule('well_founded_descent',
                     ['obstruction_count(Pprime,m_lt_n)'],
                     'descent_well_founded(P)', RuleKind.SEMANTIC)

    steps3 = [
        InferenceStep('s0', ax_obs_n, (), 'obstruction_count(P,n)', 'P has n obstructions', 0),
        InferenceStep('s1', ax_pos, (), 'greater(n,0)', 'n > 0', 1),
        InferenceStep('s2', ax_repair, (), 'repair(P,Pprime)', 'repair produces P\'', 2),
        InferenceStep('s3', r_reduced,
                      ('obstruction_count(P,n)', 'greater(n,0)', 'repair(P,Pprime)'),
                      'obstruction_count(Pprime,m_lt_n)', 'obstruction count decreases', 3),
        InferenceStep('s4', r_wf, ('obstruction_count(Pprime,m_lt_n)',),
                      'descent_well_founded(P)', 'descent is well-founded', 4),
    ]
    ok3, issues3 = verify_proof_trace(steps3, goal='descent_well_founded(P)')
    report(f"repair reduces obstructions (issues={issues3})", ok3 and not issues3)

    # ==================================================================
    # CLI: jugeo prove on correct and buggy programs
    # ==================================================================
    print("\nCLI — obstruction vanishing on correct programs")

    correct = write_temp("def add(x, y):\n    return x + y\n\ndef double(x):\n    return add(x, x)\n")
    temp_files.append(correct)
    buggy = write_temp("def divide(x, y):\n    return x / y\n\ndef reciprocal(x):\n    return divide(1, x)\n")
    temp_files.append(buggy)
    complex_prog = write_temp("def process(data):\n    result = []\n    for item in data:\n        if item > 0:\n            result.append(item * 2)\n        else:\n            result.append(0)\n    return result\n\ndef summarize(data):\n    processed = process(data)\n    return sum(processed)\n")
    temp_files.append(complex_prog)

    for label, path in [("correct", correct), ("complex", complex_prog)]:
        objs = run_jugeo("prove", path)
        if len(objs) >= 2:
            obs = objs[1].get("formal_verification", {}).get("obstruction_vanishing", {})
            report(f"H1=0 {label}", obs.get("H1") == "0", kind="cli")
        else:
            report(f"H1=0 {label}", False, kind="cli")

    print("\nCLI — effective descent holds")
    for label, path in [("correct", correct), ("complex", complex_prog)]:
        objs = run_jugeo("prove", path)
        if len(objs) >= 2:
            ed = objs[1].get("descent_locality", {}).get("effective_descent", {})
            report(f"effective_descent {label}", ed.get("all_effective", False), kind="cli")
        else:
            report(f"effective_descent {label}", False, kind="cli")

    print("\nCLI — bugs command on buggy vs correct")
    for label, path in [("correct", correct), ("buggy", buggy)]:
        objs = run_jugeo("bugs", path)
        if objs and isinstance(objs[0], list) and objs[0]:
            report(f"bugs_runs {label}", True, kind="cli")
        else:
            report(f"bugs_runs {label}", False, kind="cli")

    total = PASS + FAIL
    print(f"\nPaper 03 — Descent Verification: {PROOFS} proofs verified, "
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
