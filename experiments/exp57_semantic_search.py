#!/usr/bin/env python3
"""Paper 57 Experiment — Semantic Search via Sheaf-Indexed Code Search.

Runs JuGeo on diverse programs, building a search index from coordinate
properties, SMT assertions, and classification data.  Measures semantic
richness, structural similarity, and cross-program coverage.
Generates papers/data-paper57.tex with \\ppLVII... macros.

Re-run: python3 experiments/exp57_semantic_search.py
"""
import json, os, subprocess, sys, tempfile, time, statistics, textwrap
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper57.tex"

# ─── Helpers ────────────────────────────────────────────────────────────────

def run_jugeo_json(*args, timeout=30):
    cmd = [sys.executable, "-m", "jugeo", "--format", "json"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    lines = [l for l in r.stdout.splitlines()
             if not (len(l) > 8 and l[2] == ':' and l[5] == ':') and not l.startswith("JuGeo v")]
    text = "\n".join(lines)
    objects = []
    dec = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        remaining = text[idx:].lstrip()
        if not remaining:
            break
        try:
            obj, end = dec.raw_decode(remaining)
            objects.append(obj)
            idx += len(text) - len(remaining) + end
        except json.JSONDecodeError:
            break
    return objects

def write_temp(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source); f.close()
    return f.name

def safe_mean(xs): return round(statistics.mean(xs), 2) if xs else 0.0
def safe_median(xs): return round(statistics.median(xs), 2) if xs else 0.0
def safe_stdev(xs): return round(statistics.stdev(xs), 2) if len(xs) > 1 else 0.0

# ─── 10 Diverse Test Programs ──────────────────────────────────────────────

PROGRAMS = {
    "matrix_multiply": textwrap.dedent("""\
        def mat_mul(a, b):
            rows_a, cols_a = len(a), len(a[0])
            cols_b = len(b[0])
            result = [[0] * cols_b for _ in range(rows_a)]
            for i in range(rows_a):
                for j in range(cols_b):
                    for k in range(cols_a):
                        result[i][j] += a[i][k] * b[k][j]
            return result
        def transpose(m):
            if not m:
                return []
            return [[m[j][i] for j in range(len(m))] for i in range(len(m[0]))]
        def identity(n):
            return [[1 if i == j else 0 for j in range(n)] for i in range(n)]
    """),
    "string_pattern_match": textwrap.dedent("""\
        def naive_search(text, pattern):
            results = []
            n, m = len(text), len(pattern)
            for i in range(n - m + 1):
                match = True
                for j in range(m):
                    if text[i + j] != pattern[j]:
                        match = False
                        break
                if match:
                    results.append(i)
            return results
        def count_occurrences(text, pattern):
            return len(naive_search(text, pattern))
        def first_occurrence(text, pattern):
            hits = naive_search(text, pattern)
            return hits[0] if hits else -1
    """),
    "hash_table": textwrap.dedent("""\
        class HashTable:
            def __init__(self, size=16):
                self.size = size
                self.buckets = [[] for _ in range(size)]
                self.count = 0
            def _hash(self, key):
                return hash(key) % self.size
            def put(self, key, value):
                idx = self._hash(key)
                for i, (k, v) in enumerate(self.buckets[idx]):
                    if k == key:
                        self.buckets[idx][i] = (key, value)
                        return
                self.buckets[idx].append((key, value))
                self.count += 1
            def get(self, key, default=None):
                idx = self._hash(key)
                for k, v in self.buckets[idx]:
                    if k == key:
                        return v
                return default
            def delete(self, key):
                idx = self._hash(key)
                for i, (k, v) in enumerate(self.buckets[idx]):
                    if k == key:
                        del self.buckets[idx][i]
                        self.count -= 1
                        return True
                return False
            def keys(self):
                result = []
                for bucket in self.buckets:
                    for k, v in bucket:
                        result.append(k)
                return result
    """),
    "binary_search_tree": textwrap.dedent("""\
        class BSTNode:
            def __init__(self, key, left=None, right=None):
                self.key = key
                self.left = left
                self.right = right
        def insert(root, key):
            if root is None:
                return BSTNode(key)
            if key < root.key:
                root.left = insert(root.left, key)
            elif key > root.key:
                root.right = insert(root.right, key)
            return root
        def search(root, key):
            if root is None or root.key == key:
                return root
            if key < root.key:
                return search(root.left, key)
            return search(root.right, key)
        def inorder(root):
            if root is None:
                return []
            return inorder(root.left) + [root.key] + inorder(root.right)
        def tree_min(root):
            while root.left:
                root = root.left
            return root.key
    """),
    "regex_matcher": textwrap.dedent("""\
        def match(pattern, text):
            if not pattern:
                return not text
            if len(pattern) >= 2 and pattern[1] == '*':
                if match(pattern[2:], text):
                    return True
                if text and (pattern[0] == '.' or pattern[0] == text[0]):
                    return match(pattern, text[1:])
                return False
            if text and (pattern[0] == '.' or pattern[0] == text[0]):
                return match(pattern[1:], text[1:])
            return False
        def is_match(pattern, text):
            return match('^' + pattern if not pattern.startswith('^') else pattern,
                         text)
        def find_all(pattern, text):
            results = []
            for i in range(len(text)):
                if match(pattern, text[i:]):
                    results.append(i)
            return results
    """),
    "csv_parser": textwrap.dedent("""\
        def parse_line(line, delimiter=','):
            fields = []
            current = []
            in_quotes = False
            for ch in line:
                if ch == '"':
                    in_quotes = not in_quotes
                elif ch == delimiter and not in_quotes:
                    fields.append(''.join(current))
                    current = []
                else:
                    current.append(ch)
            fields.append(''.join(current))
            return fields
        def parse_csv(text, delimiter=','):
            lines = text.strip().split('\\n')
            if not lines:
                return []
            headers = parse_line(lines[0], delimiter)
            rows = []
            for line in lines[1:]:
                values = parse_line(line, delimiter)
                row = dict(zip(headers, values))
                rows.append(row)
            return rows
        def to_csv(rows, delimiter=','):
            if not rows:
                return ''
            headers = list(rows[0].keys())
            lines = [delimiter.join(headers)]
            for row in rows:
                lines.append(delimiter.join(str(row.get(h, '')) for h in headers))
            return '\\n'.join(lines)
    """),
    "http_request_builder": textwrap.dedent("""\
        class Request:
            def __init__(self, method, url):
                self.method = method.upper()
                self.url = url
                self.headers = {}
                self.body = None
                self.params = {}
            def header(self, key, value):
                self.headers[key] = value
                return self
            def param(self, key, value):
                self.params[key] = str(value)
                return self
            def json_body(self, data):
                self.body = data
                self.headers['Content-Type'] = 'application/json'
                return self
            def build_url(self):
                if not self.params:
                    return self.url
                qs = '&'.join(f'{k}={v}' for k, v in self.params.items())
                sep = '&' if '?' in self.url else '?'
                return self.url + sep + qs
            def __repr__(self):
                return f'{self.method} {self.build_url()}'
        def get(url):
            return Request('GET', url)
        def post(url, body=None):
            r = Request('POST', url)
            if body is not None:
                r.json_body(body)
            return r
    """),
    "event_emitter": textwrap.dedent("""\
        class EventEmitter:
            def __init__(self):
                self._listeners = {}
            def on(self, event, callback):
                if event not in self._listeners:
                    self._listeners[event] = []
                self._listeners[event].append(callback)
                return self
            def off(self, event, callback):
                if event in self._listeners:
                    self._listeners[event] = [
                        cb for cb in self._listeners[event] if cb != callback
                    ]
                return self
            def emit(self, event, *args, **kwargs):
                for cb in self._listeners.get(event, []):
                    cb(*args, **kwargs)
            def once(self, event, callback):
                def wrapper(*args, **kwargs):
                    callback(*args, **kwargs)
                    self.off(event, wrapper)
                self.on(event, wrapper)
                return self
            def listener_count(self, event):
                return len(self._listeners.get(event, []))
    """),
    "command_pattern": textwrap.dedent("""\
        class CommandHistory:
            def __init__(self):
                self._done = []
                self._undone = []
            def execute(self, command):
                command.execute()
                self._done.append(command)
                self._undone.clear()
            def undo(self):
                if not self._done:
                    raise IndexError("nothing to undo")
                cmd = self._done.pop()
                cmd.undo()
                self._undone.append(cmd)
            def redo(self):
                if not self._undone:
                    raise IndexError("nothing to redo")
                cmd = self._undone.pop()
                cmd.execute()
                self._done.append(cmd)
            def can_undo(self):
                return len(self._done) > 0
            def can_redo(self):
                return len(self._undone) > 0
        class SetValueCommand:
            def __init__(self, target, attr, value):
                self.target = target
                self.attr = attr
                self.value = value
                self.old_value = None
            def execute(self):
                self.old_value = getattr(self.target, self.attr, None)
                setattr(self.target, self.attr, self.value)
            def undo(self):
                setattr(self.target, self.attr, self.old_value)
    """),
    "iterator_utils": textwrap.dedent("""\
        def take(iterable, n):
            result = []
            for i, item in enumerate(iterable):
                if i >= n:
                    break
                result.append(item)
            return result
        def drop(iterable, n):
            result = []
            for i, item in enumerate(iterable):
                if i >= n:
                    result.append(item)
            return result
        def chunked(iterable, size):
            chunk = []
            for item in iterable:
                chunk.append(item)
                if len(chunk) == size:
                    yield list(chunk)
                    chunk.clear()
            if chunk:
                yield list(chunk)
        def flatten(nested):
            for item in nested:
                if isinstance(item, (list, tuple)):
                    yield from flatten(item)
                else:
                    yield item
        def unique(iterable):
            seen = set()
            for item in iterable:
                if item not in seen:
                    seen.add(item)
                    yield item
    """),
}

# ─── Run experiments ────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Paper 57: Semantic Search Experiments")
    print("=" * 60)

    results = []
    category_counter = Counter()

    for prog_id, source in PROGRAMS.items():
        print(f"  [{prog_id}] ...", end=" ", flush=True)
        tmp = write_temp(source)
        try:
            # 1. load — site structure
            t_load = time.perf_counter()
            load_objs = run_jugeo_json("load", tmp)
            t_load = time.perf_counter() - t_load
            load_data = load_objs[0] if load_objs else {}

            summary = load_data.get("summary", {})
            n_coords = summary.get("coordinates", 0)
            n_morphisms = summary.get("morphisms", 0)
            n_covering = summary.get("covering_families", 0)
            n_judgments = summary.get("judgments", 0)
            n_bindings = summary.get("context_bindings", 0)

            # Count functions and classes from coordinate details
            coord_list = load_data.get("coordinates", [])
            n_functions = sum(1 for c in coord_list if c.get("kind", "").upper() == "FUNCTION")
            n_classes = sum(1 for c in coord_list if c.get("kind", "").upper() in ("INTERFACE", "CLASS"))

            # 2. encode — SMT encodings per coordinate
            t_enc = time.perf_counter()
            enc_objs = run_jugeo_json("encode", tmp)
            t_enc = time.perf_counter() - t_enc
            enc_data = enc_objs[0] if enc_objs else {}

            files_enc = enc_data.get("files", [])
            coord_encodings = files_enc[0].get("coordinates", {}) if files_enc else {}
            total_assertions = 0
            total_declarations = 0
            decidable_coords = 0
            boundary_coords = 0
            assertions_per_coord = []
            for cname, cdata in coord_encodings.items():
                a = cdata.get("assertions", 0)
                d = cdata.get("declarations", 0)
                total_assertions += a
                total_declarations += d
                assertions_per_coord.append(a)
                if cdata.get("decidability", "unknown") == "decidable":
                    decidable_coords += 1
                if cdata.get("frontier_side", "unknown") in ("boundary", "outside"):
                    boundary_coords += 1

            # 3. classify — category and confidence
            t_cls = time.perf_counter()
            cls_objs = run_jugeo_json("classify", tmp)
            t_cls = time.perf_counter() - t_cls
            cls_data = cls_objs[0] if cls_objs else {}

            classification = cls_data.get("classification", {})
            category = classification.get("category", "unknown")
            confidence = classification.get("confidence", 0.0)
            geo_char = classification.get("geometric_characterization", "")
            recommended = cls_data.get("recommended_subsystems", [])
            category_counter[category] += 1

            # 4. evaluate — per-coordinate quality
            t_eval = time.perf_counter()
            eval_objs = run_jugeo_json("evaluate", tmp)
            t_eval = time.perf_counter() - t_eval
            eval_data = eval_objs[0] if eval_objs else {}

            per_coord = eval_data.get("per_coordinate", [])
            qualities = [c.get("quality", 0.0) for c in per_coord]
            complexities = [c.get("complexity", 0.0) for c in per_coord]
            coord_functions = sum(c.get("functions", 0) for c in per_coord)
            coord_lines = sum(c.get("lines", 0) for c in per_coord)

            trust_data = eval_data.get("trust", {})
            agg_trust = trust_data.get("aggregate_trust", 0.0) if isinstance(trust_data, dict) else 0.0

            rec = {
                "id": prog_id,
                "n_coords": n_coords,
                "n_morphisms": n_morphisms,
                "n_covering": n_covering,
                "n_judgments": n_judgments,
                "n_bindings": n_bindings,
                "n_functions": n_functions,
                "n_classes": n_classes,
                "total_assertions": total_assertions,
                "total_declarations": total_declarations,
                "decidable_coords": decidable_coords,
                "boundary_coords": boundary_coords,
                "mean_assertions_per_coord": safe_mean(assertions_per_coord),
                "category": category,
                "confidence": round(confidence, 4),
                "geo_characterization": geo_char,
                "recommended_subsystems": recommended,
                "mean_quality": safe_mean(qualities),
                "mean_complexity": safe_mean(complexities),
                "coord_functions": coord_functions,
                "coord_lines": coord_lines,
                "agg_trust": agg_trust,
                "time_load": round(t_load, 3),
                "time_encode": round(t_enc, 3),
                "time_classify": round(t_cls, 3),
                "time_evaluate": round(t_eval, 3),
            }
            results.append(rec)
            print(f"coords={n_coords} morph={n_morphisms} asserts={total_assertions} "
                  f"cat={category} q={safe_mean(qualities)} t={t_load+t_enc+t_cls+t_eval:.2f}s")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({"id": prog_id, "error": str(e)})
        finally:
            try: os.unlink(tmp)
            except OSError: pass

    # ─── Compute aggregates ─────────────────────────────────────────────────

    ok = [r for r in results if "error" not in r]
    n_total = len(PROGRAMS)
    n_ok = len(ok)

    coords_list = [r["n_coords"] for r in ok]
    morph_list = [r["n_morphisms"] for r in ok]
    assert_list = [r["total_assertions"] for r in ok]
    mean_assert_per_coord = [r["mean_assertions_per_coord"] for r in ok]
    decidable_list = [r["decidable_coords"] for r in ok]
    boundary_list = [r["boundary_coords"] for r in ok]
    quality_list = [r["mean_quality"] for r in ok]
    complexity_list = [r["mean_complexity"] for r in ok]
    func_list = [r["n_functions"] for r in ok]
    class_list = [r["n_classes"] for r in ok]
    time_load_list = [r["time_load"] for r in ok]
    time_enc_list = [r["time_encode"] for r in ok]
    time_cls_list = [r["time_classify"] for r in ok]

    total_functions = sum(func_list)
    total_classes = sum(class_list)
    total_assertions_all = sum(assert_list)
    total_decidable = sum(decidable_list)
    total_boundary = sum(boundary_list)
    top_category = category_counter.most_common(1)[0][0] if category_counter else "unknown"

    # ─── Search index analysis ──────────────────────────────────────────────

    print(f"\n  Search Index Summary:")
    print(f"    Programs indexed: {n_ok}")
    print(f"    Total coordinates: {sum(coords_list)}")
    print(f"    Total assertions: {total_assertions_all}")
    print(f"    Top category: {top_category}")

    # Find richest program by assertion count
    richest = max(ok, key=lambda r: r["total_assertions"]) if ok else {}
    print(f"    Richest program: {richest.get('id', 'N/A')} "
          f"({richest.get('total_assertions', 0)} assertions)")

    # ─── Generate LaTeX macros ──────────────────────────────────────────────

    print(f"\nGenerating {TEX_PATH}")
    lines = [
        "% data-paper57.tex — AUTO-GENERATED by exp57_semantic_search.py",
        "% DO NOT EDIT — regenerate with: python3 experiments/exp57_semantic_search.py",
        f"% Generated from {n_total} programs",
        "",
        "% ── Overall statistics ──────────────────────────────────────────",
        f"\\newcommand{{\\ppLVIItotalPrograms}}{{{n_total}}}",
        f"\\newcommand{{\\ppLVIImeanBuildTime}}{{{safe_mean(time_load_list)}\\,s}}",
        f"\\newcommand{{\\ppLVIImeanCoords}}{{{safe_mean(coords_list)}}}",
        f"\\newcommand{{\\ppLVIImeanMorphisms}}{{{safe_mean(morph_list)}}}",
        f"\\newcommand{{\\ppLVIItotalFunctions}}{{{total_functions}}}",
        f"\\newcommand{{\\ppLVIItotalClasses}}{{{total_classes}}}",
        "",
        "% ── SMT encoding metrics ───────────────────────────────────────",
        f"\\newcommand{{\\ppLVIImeanAssertions}}{{{safe_mean(mean_assert_per_coord)}}}",
        f"\\newcommand{{\\ppLVIItotalAssertions}}{{{total_assertions_all}}}",
        f"\\newcommand{{\\ppLVIIdecidableCoords}}{{{total_decidable}}}",
        f"\\newcommand{{\\ppLVIIboundaryCoords}}{{{total_boundary}}}",
        "",
        "% ── Timing metrics ─────────────────────────────────────────────",
        f"\\newcommand{{\\ppLVIImeanClassifyTime}}{{{safe_mean(time_cls_list)}\\,s}}",
        f"\\newcommand{{\\ppLVIImeanEncodeTime}}{{{safe_mean(time_enc_list)}\\,s}}",
        "",
        "% ── Classification and quality ─────────────────────────────────",
        f"\\newcommand{{\\ppLVIItopCategory}}{{{top_category}}}",
        f"\\newcommand{{\\ppLVIImeanQuality}}{{{safe_mean(quality_list)}}}",
        f"\\newcommand{{\\ppLVIImeanComplexity}}{{{safe_mean(complexity_list)}}}",
        "",
        "% ── Per-program search-index data ──────────────────────────────",
    ]

    for r in ok:
        tag = r["id"].replace("_", "")
        lines.append(f"\\newcommand{{\\ppLVIIsrch{tag}Coords}}{{{r['n_coords']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIsrch{tag}Morphisms}}{{{r['n_morphisms']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIsrch{tag}Assertions}}{{{r['total_assertions']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIsrch{tag}Category}}{{{r['category']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIsrch{tag}Quality}}{{{r['mean_quality']}}}")
        lines.append(f"\\newcommand{{\\ppLVIIsrch{tag}LoadTime}}{{{r['time_load']}\\,s}}")

    PAPERS.mkdir(parents=True, exist_ok=True)
    with open(TEX_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    # Save JSON results
    json_path = ROOT / "experiments" / "results_paper57.json"
    with open(json_path, "w") as f:
        json.dump({
            "paper": 57,
            "programs": n_total,
            "programs_ok": n_ok,
            "top_category": top_category,
            "category_distribution": dict(category_counter),
            "results": results,
        }, f, indent=2, default=str)

    macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
    print(f"  Wrote {macro_count} macros to {TEX_PATH}")
    print(f"  Wrote results to {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
