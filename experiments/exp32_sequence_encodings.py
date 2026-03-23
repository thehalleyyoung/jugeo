#!/usr/bin/env python3
"""Paper 32 Experiment — Sequence/Mutation Encodings.

Measures encoding and solving time per mutation operation type,
plus agreement with CPython execution.

Outputs: papers/data-paper32.tex  (LaTeX macros with \\ppXXXII… prefix)
Re-run:  python3 experiments/exp32_sequence_encodings.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

PROGRAMS = [
    {"id": "append_loop", "op": "MutApp", "code": """
def build_list(n):
    xs = []
    for i in range(n):
        xs.append(i * 2)
    return xs
"""},
    {"id": "insert_mid", "op": "MutIns", "code": """
def insert_sorted(xs, val):
    for i, x in enumerate(xs):
        if val < x:
            xs.insert(i, val)
            return xs
    xs.append(val)
    return xs
"""},
    {"id": "pop_stack", "op": "MutPop", "code": """
def drain_stack(xs):
    result = []
    while xs:
        result.append(xs.pop())
    return result
"""},
    {"id": "slice_window", "op": "MutSlice", "code": """
def sliding_window(xs, k):
    windows = []
    for i in range(len(xs) - k + 1):
        windows.append(xs[i:i+k])
    return windows
"""},
    {"id": "assign_update", "op": "MutAssign", "code": """
def update_all(xs, old, new):
    for i in range(len(xs)):
        if xs[i] == old:
            xs[i] = new
    return xs
"""},
    {"id": "dict_insert", "op": "DictIns", "code": """
def count_words(words):
    freq = {}
    for w in words:
        if w in freq:
            freq[w] += 1
        else:
            freq[w] = 1
    return freq
"""},
    {"id": "dict_delete", "op": "DictDel", "code": """
def remove_keys(d, keys):
    for k in keys:
        if k in d:
            del d[k]
    return d
"""},
    {"id": "alias_mut", "op": "Alias", "code": """
def alias_append(xs):
    ys = xs
    ys.append(42)
    return xs, ys
"""},
    {"id": "nested_list", "op": "MutApp", "code": """
def build_matrix(n, m):
    mat = []
    for i in range(n):
        row = []
        for j in range(m):
            row.append(i * m + j)
        mat.append(row)
    return mat
"""},
    {"id": "set_ops", "op": "MutApp", "code": """
def unique_sorted(xs):
    seen = set()
    result = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return sorted(result)
"""},
]

OP_NAMES = ["MutApp", "MutIns", "MutPop", "MutSlice", "MutAssign",
            "DictIns", "DictDel", "Alias"]


def run_jugeo(*args):
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=30)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = decoder.raw_decode(remaining)
            return obj
        except json.JSONDecodeError:
            break
    return {}


def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def main():
    from jugeo.geometry import SiteBuilder
    from jugeo.maturity import CyclicSystemCoordinator

    op_enc_times = {op: [] for op in OP_NAMES}
    op_solve_times = {op: [] for op in OP_NAMES}
    op_agreements = {op: [] for op in OP_NAMES}

    for prog in PROGRAMS:
        tmp = write_temp(prog["code"])

        # Encode
        t0 = time.perf_counter()
        enc = run_jugeo("encode", tmp)
        enc_s = time.perf_counter() - t0

        # Evaluate (solve)
        t1 = time.perf_counter()
        ev = run_jugeo("evaluate", tmp)
        solve_s = time.perf_counter() - t1

        # Descend for verdict
        desc_objs = run_jugeo("descend", tmp)
        desc = desc_objs if isinstance(desc_objs, dict) else (desc_objs[0] if isinstance(desc_objs, list) and desc_objs else {})
        verdict = desc.get("verdict", "unknown")
        agreed = 1 if verdict == "verified" else 0

        # Cyclic metrics
        coord = CyclicSystemCoordinator.create(prog["id"])
        coord.run_full_cycle({"source": prog["code"]})
        mets = coord.get_metrics().to_dict()

        op = prog["op"]
        enc_ms = round(enc_s * 1000, 2)
        solve_ms = round(solve_s * 1000, 2)

        op_enc_times[op].append(enc_ms)
        op_solve_times[op].append(solve_ms)
        op_agreements[op].append(agreed)

        cleanup(tmp)
        print(f"  {prog['id']:16s}  op={op:10s}  enc={enc_ms:6.1f}ms  "
              f"solve={solve_ms:6.1f}ms  agreed={agreed}")

    # Aggregate per operation
    op_agg = {}
    for op in OP_NAMES:
        e = op_enc_times[op]
        s = op_solve_times[op]
        a = op_agreements[op]
        op_agg[op] = {
            "median_enc": round(statistics.median(e), 1) if e else 0,
            "median_solve": round(statistics.median(s), 1) if s else 0,
            "agreement": round(sum(a) / len(a) * 100, 1) if a else 0,
        }

    # Overall
    all_enc = [t for ts in op_enc_times.values() for t in ts]
    all_solve = [t for ts in op_solve_times.values() for t in ts]
    all_agree = [a for ag in op_agreements.values() for a in ag]
    overall = {
        "median_enc": round(statistics.median(all_enc), 1),
        "median_solve": round(statistics.median(all_solve), 1),
        "agreement": round(sum(all_agree) / len(all_agree) * 100, 1),
    }

    print("\n" + "=" * 60)
    print("OPERATION SUMMARY")
    for op in OP_NAMES:
        a = op_agg[op]
        print(f"  {op:12s}  enc={a['median_enc']:6.1f}ms  solve={a['median_solve']:6.1f}ms  "
              f"agree={a['agreement']:.1f}%")
    print(f"  {'OVERALL':12s}  enc={overall['median_enc']:6.1f}ms  solve={overall['median_solve']:6.1f}ms  "
          f"agree={overall['agreement']:.1f}%")

    # Generate LaTeX macros
    P = "ppXXXII"
    tex = [
        f"% data-paper32.tex — AUTO-GENERATED by exp32_sequence_encodings.py",
        f"% DO NOT EDIT — regenerate with: python3 experiments/exp32_sequence_encodings.py",
        f"",
    ]

    def m(name, val):
        tex.append(f"\\newcommand{{\\{P}{name}}}{{{val}}}")

    safe_names = {
        "MutApp": "App", "MutIns": "Ins", "MutPop": "Pop",
        "MutSlice": "Slice", "MutAssign": "Assign",
        "DictIns": "DictIns", "DictDel": "DictDel", "Alias": "Alias",
    }
    for op in OP_NAMES:
        sn = safe_names[op]
        a = op_agg[op]
        m(f"{sn}Enc", f"{a['median_enc']}\\,ms")
        m(f"{sn}Solve", f"{a['median_solve']}\\,ms")
        m(f"{sn}Agree", f"{a['agreement']}\\%")

    m("OverallEnc", f"{overall['median_enc']}\\,ms")
    m("OverallSolve", f"{overall['median_solve']}\\,ms")
    m("OverallAgree", f"{overall['agreement']}\\%")
    m("TotalPrograms", len(PROGRAMS))

    tex_path = os.path.join(ROOT, "papers", "data-paper32.tex")
    with open(tex_path, "w") as f:
        f.write("\n".join(tex) + "\n")
    print(f"\nWrote {tex_path}")

    json_path = os.path.join(os.path.dirname(__file__), "results_paper32.json")
    with open(json_path, "w") as f:
        json.dump({"per_op": op_agg, "overall": overall}, f, indent=2)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
