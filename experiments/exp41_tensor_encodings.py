#!/usr/bin/env python3
"""Paper 41 Experiment — Tensor / Quantifier Encodings.

Encodes diverse programs into Z3/SMT, classifies fragments,
and measures encoding generation statistics.

Outputs: papers/data-paper41.tex  (LaTeX macros with \\ppFortyOne… prefix)
Re-run:  python3 experiments/exp41_tensor_encodings.py
"""
import subprocess, json, os, tempfile, time, statistics

ROOT = os.path.join(os.path.dirname(__file__), "..")

PROGRAMS = [
    {"id": "matmul", "code": """
def matmul(A, B):
    rows_A, cols_A = len(A), len(A[0])
    rows_B, cols_B = len(B), len(B[0])
    assert cols_A == rows_B, "incompatible shapes"
    C = [[0] * cols_B for _ in range(rows_A)]
    for i in range(rows_A):
        for j in range(cols_B):
            for k in range(cols_A):
                C[i][j] += A[i][k] * B[k][j]
    return C
"""},
    {"id": "dot_product", "code": """
def dot(u, v):
    assert len(u) == len(v), "length mismatch"
    return sum(a * b for a, b in zip(u, v))

def cosine_similarity(u, v):
    d = dot(u, v)
    nu = sum(x * x for x in u) ** 0.5
    nv = sum(x * x for x in v) ** 0.5
    if nu == 0 or nv == 0:
        return 0.0
    return d / (nu * nv)
"""},
    {"id": "conv_one_d", "code": """
def conv1d(signal, kernel):
    n, k = len(signal), len(kernel)
    out = []
    for i in range(n - k + 1):
        acc = 0.0
        for j in range(k):
            acc += signal[i + j] * kernel[j]
        out.append(acc)
    return out
"""},
    {"id": "transpose_reshape", "code": """
def transpose(matrix):
    if not matrix:
        return []
    rows, cols = len(matrix), len(matrix[0])
    return [[matrix[i][j] for i in range(rows)] for j in range(cols)]

def reshape(flat, rows, cols):
    assert len(flat) == rows * cols
    return [flat[i * cols:(i + 1) * cols] for i in range(rows)]
"""},
    {"id": "softmax", "code": """
import math

def softmax(xs):
    m = max(xs)
    exps = [math.exp(x - m) for x in xs]
    s = sum(exps)
    return [e / s for e in exps]

def cross_entropy(pred, target):
    eps = 1e-12
    return -sum(t * math.log(p + eps) for p, t in zip(pred, target))
"""},
    {"id": "batch_norm", "code": """
def batch_norm(xs, gamma=1.0, beta=0.0, eps=1e-5):
    n = len(xs)
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / n
    inv_std = 1.0 / (var + eps) ** 0.5
    return [gamma * (x - mu) * inv_std + beta for x in xs]
"""},
    {"id": "attention_score", "code": """
def attention_scores(query, keys):
    d = len(query)
    scale = d ** 0.5
    scores = []
    for k in keys:
        dot = sum(q * ki for q, ki in zip(query, k))
        scores.append(dot / scale)
    return scores

def weighted_sum(weights, values):
    dim = len(values[0])
    out = [0.0] * dim
    for w, v in zip(weights, values):
        for j in range(dim):
            out[j] += w * v[j]
    return out
"""},
    {"id": "pooling", "code": """
def max_pool(matrix, pool_size):
    rows, cols = len(matrix), len(matrix[0])
    out = []
    for i in range(0, rows - pool_size + 1, pool_size):
        row = []
        for j in range(0, cols - pool_size + 1, pool_size):
            patch = [matrix[i + di][j + dj]
                     for di in range(pool_size)
                     for dj in range(pool_size)]
            row.append(max(patch))
        out.append(row)
    return out
"""},
    {"id": "tensor_add_scale", "code": """
def elementwise_add(A, B):
    assert len(A) == len(B) and len(A[0]) == len(B[0])
    return [[A[i][j] + B[i][j] for j in range(len(A[0]))]
            for i in range(len(A))]

def scale(A, factor):
    return [[A[i][j] * factor for j in range(len(A[0]))]
            for i in range(len(A))]

def frobenius_norm(A):
    return sum(A[i][j] ** 2 for i in range(len(A))
               for j in range(len(A[0]))) ** 0.5
"""},
    {"id": "linear_layer", "code": """
def linear(x, W, b):
    out_dim = len(W)
    in_dim = len(W[0])
    assert len(x) == in_dim
    assert len(b) == out_dim
    result = []
    for i in range(out_dim):
        val = b[i]
        for j in range(in_dim):
            val += W[i][j] * x[j]
        result.append(val)
    return result

def relu(xs):
    return [max(0.0, x) for x in xs]
"""},
]


def run_jugeo(*args):
    """Run jugeo CLI and parse JSON output."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=60)
    lines = [l for l in result.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':')
             and not l.startswith("JuGeo v")]
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
            idx += len(text[idx:]) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def fmt_time(secs):
    if secs < 0.001:
        return f"{secs*1_000_000:.0f}\\,\\mu s"
    if secs < 1.0:
        return f"{secs*1000:.1f}\\,ms"
    return f"{secs:.2f}\\,s"


def fmt_pct(val):
    return f"{val*100:.1f}\\%"


def safe_mean(lst):
    return statistics.mean(lst) if lst else 0.0


def safe_median(lst):
    return statistics.median(lst) if lst else 0.0


def emit_latex(macros, path):
    """Write LaTeX macro file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write("% data-paper41.tex — AUTO-GENERATED by exp41_tensor_encodings.py\n")
        f.write("% DO NOT EDIT — regenerate with: python3 experiments/exp41_tensor_encodings.py\n\n")
        for name, value in macros:
            f.write(f"\\newcommand{{\\{name}}}{{{value}}}\n")
    print(f"Wrote {len(macros)} macros to {path}")


def main():
    from jugeo.solver.fragments import FragmentClassifier, Fragment
    from jugeo.geometry import SiteBuilder

    classifier = FragmentClassifier()

    # Accumulators
    all_coords = []
    all_decls = []
    all_asserts = []
    enc_times = []
    fragment_counts = {f.name: 0 for f in Fragment}
    total_encodings = 0
    batch_count = 0

    print("=" * 60)
    print("PAPER 41 — Tensor / Quantifier Encodings")
    print("=" * 60)

    for prog in PROGRAMS:
        tmp = write_temp_py(prog["code"])

        # Time the encode call
        t0 = time.perf_counter()
        results = run_jugeo("encode", tmp, "--encoding", "all")
        enc_time = time.perf_counter() - t0
        enc_times.append(enc_time)

        enc = results[0] if results else {}
        totals = enc.get("totals", {})
        n_coords = totals.get("coordinates", 0)
        n_decls = totals.get("declarations", 0)
        n_asserts = totals.get("assertions", 0)
        all_coords.append(n_coords)
        all_decls.append(n_decls)
        all_asserts.append(n_asserts)

        # Count encodings (one per coordinate)
        total_encodings += n_coords

        # Classify formulas via FragmentClassifier
        files = enc.get("files", [])
        for finfo in files:
            for coord_name, coord_data in finfo.get("coordinates", {}).items():
                smt_assertions = coord_data.get("smt2_assertions", [])
                if smt_assertions:
                    frags = classifier.classify_batch(smt_assertions)
                    batch_count += len(frags)
                    for frag in frags:
                        fragment_counts[frag.name] += 1
                else:
                    # Classify by decidability field
                    dec = coord_data.get("decidability", "unknown")
                    if dec == "trivial":
                        fragment_counts["QF_UF"] += 1
                    elif dec in fragment_counts:
                        fragment_counts[dec] += 1
                    else:
                        fragment_counts["QF_UF"] += 1
                    batch_count += 1

        # Also build a Site for structural data
        site = SiteBuilder(prog["code"]).build()
        site_enc = site.encode_for_solver()
        site_n = site_enc.get("coordinate_count", 0)

        cleanup(tmp)
        print(f"  {prog['id']:20s}  coords={n_coords:2d}  decls={n_decls:3d}  "
              f"asserts={n_asserts:3d}  time={enc_time:.3f}s")

    # Aggregate
    mean_coords = round(safe_mean(all_coords), 1)
    mean_decls = round(safe_mean(all_decls), 1)
    mean_asserts = round(safe_mean(all_asserts), 1)
    mean_enc_time = safe_mean(enc_times)
    median_enc_time = safe_median(enc_times)

    # Quantifier-free fraction
    qf_count = sum(fragment_counts[f.name] for f in Fragment
                   if f.name.startswith("QF_"))
    total_classified = max(batch_count, 1)
    discipline_qf = qf_count / total_classified

    print()
    print("=" * 60)
    print("SUMMARY")
    print(f"  Total programs     : {len(PROGRAMS)}")
    print(f"  Total encodings    : {total_encodings}")
    print(f"  Mean coords/prog   : {mean_coords}")
    print(f"  Mean decls/enc     : {mean_decls}")
    print(f"  Mean asserts/enc   : {mean_asserts}")
    print(f"  Mean encode time   : {mean_enc_time:.4f}s")
    print(f"  Median encode time : {median_enc_time:.4f}s")
    print(f"  Fragment counts    : {dict((k, v) for k, v in fragment_counts.items() if v > 0)}")
    print(f"  Batch classifications: {batch_count}")
    print(f"  QF discipline      : {discipline_qf:.1%}")

    # Build macros
    P = "ppFortyOne"
    macros = [
        (f"{P}TotalPrograms", str(len(PROGRAMS))),
        (f"{P}TotalEncodings", str(total_encodings)),
        (f"{P}MeanCoords", str(mean_coords)),
        (f"{P}MeanDecls", str(mean_decls)),
        (f"{P}MeanAsserts", str(mean_asserts)),
        (f"{P}MeanEncTime", fmt_time(mean_enc_time)),
        (f"{P}MedianEncTime", fmt_time(median_enc_time)),
        (f"{P}FragQFUF", str(fragment_counts.get("QF_UF", 0))),
        (f"{P}FragQFLIA", str(fragment_counts.get("QF_LIA", 0))),
        (f"{P}FragQuant", str(fragment_counts.get("QUANTIFIED", 0))),
        (f"{P}BatchCount", str(batch_count)),
        (f"{P}DisciplineQF", fmt_pct(discipline_qf)),
    ]

    tex_path = os.path.join(ROOT, "papers", "data-paper41.tex")
    emit_latex(macros, tex_path)

    # Save JSON results
    json_path = os.path.join(os.path.dirname(__file__), "results_paper41.json")
    with open(json_path, "w") as f:
        json.dump({
            "programs": len(PROGRAMS),
            "total_encodings": total_encodings,
            "mean_coords": mean_coords,
            "mean_decls": mean_decls,
            "mean_asserts": mean_asserts,
            "mean_enc_time": round(mean_enc_time, 4),
            "median_enc_time": round(median_enc_time, 4),
            "fragment_counts": {k: v for k, v in fragment_counts.items() if v > 0},
            "batch_count": batch_count,
            "discipline_qf": round(discipline_qf, 4),
        }, f, indent=2)
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
