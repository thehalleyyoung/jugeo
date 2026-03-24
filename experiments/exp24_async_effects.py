#!/usr/bin/env python3
"""
Experiment 24 -- Async Effects: Effect Boundary Analysis
========================================================

Measures how JuGeo classifies async effect boundaries across four categories:
  Pure async, Shared-state (benign), Shared-state (races), Mixed IO.

Uses CLI load/descend/bugs and SiteBuilder for site metrics.

Writes macros to papers/data-paper24.tex with prefix ppTwentyfour.
Re-run: python3 experiments/exp24_async_effects.py
"""

import subprocess, json, os, sys, tempfile, time, statistics, textwrap

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# -- CLI helper ----------------------------------------------------------------

def run_jugeo(*args, timeout=30):
    """Run jugeo CLI and parse JSON output."""
    cmd = ["python3", "-m", "jugeo", "--format", "json"] + list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT)
        lines = [l for l in result.stdout.splitlines()
                 if not (len(l) > 8 and l[2] == ':' and l[5] == ':')]
        lines = [l for l in lines if not l.startswith("JuGeo v")]
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
    except subprocess.TimeoutExpired:
        return []


def write_temp_py(source):
    f = tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False, dir='/tmp')
    f.write(source)
    f.close()
    return f.name


def write_macro(fh, name, value):
    fh.write("\\newcommand{\\" + name + "}{" + str(value) + "}\n")


def fmt_pct(val):
    return "{:.1f}\\%".format(val)


def fmt_ms(val):
    return "{:.2f}\\,\\text{{ms}}".format(val)


def fmt_int(val):
    return str(int(val))


# -- Programs ------------------------------------------------------------------
# 10 diverse programs across four effect categories

PROGRAMS = {
    # -- Pure async (no shared state, clean concurrency) -----------------------
    "async_pipeline": textwrap.dedent("""\
        import asyncio

        async def fetch_data(url):
            await asyncio.sleep(0.01)
            return {'url': url, 'data': 'content'}

        async def transform(record):
            await asyncio.sleep(0.001)
            return {**record, 'transformed': True}

        async def pipeline(urls):
            results = []
            for url in urls:
                raw = await fetch_data(url)
                out = await transform(raw)
                results.append(out)
            return results
    """),
    "async_generator": textwrap.dedent("""\
        import asyncio

        async def countdown(n):
            while n > 0:
                yield n
                n -= 1
                await asyncio.sleep(0.001)

        async def collect_countdown(n):
            results = []
            async for val in countdown(n):
                results.append(val)
            return results
    """),
    "pure_coroutine": textwrap.dedent("""\
        import asyncio

        async def compute_fibonacci(n):
            if n <= 1:
                return n
            a, b = 0, 1
            for _ in range(n - 1):
                a, b = b, a + b
                await asyncio.sleep(0)
            return b

        async def batch_fibonacci(numbers):
            return [await compute_fibonacci(n) for n in numbers]
    """),

    # -- Shared-state benign (mutation but no races) ---------------------------
    "benign_counter": textwrap.dedent("""\
        class SafeCounter:
            def __init__(self):
                self._count = 0

            def increment(self):
                self._count += 1
                return self._count

            def decrement(self):
                if self._count > 0:
                    self._count -= 1
                return self._count

            def reset(self):
                self._count = 0

            def value(self):
                return self._count
    """),
    "benign_cache": textwrap.dedent("""\
        class LRUCache:
            def __init__(self, capacity):
                self._capacity = capacity
                self._cache = {}
                self._order = []

            def get(self, key):
                if key in self._cache:
                    self._order.remove(key)
                    self._order.append(key)
                    return self._cache[key]
                return None

            def put(self, key, value):
                if key in self._cache:
                    self._order.remove(key)
                elif len(self._cache) >= self._capacity:
                    oldest = self._order.pop(0)
                    del self._cache[oldest]
                self._cache[key] = value
                self._order.append(key)

            def size(self):
                return len(self._cache)
    """),

    # -- Shared-state races (potential concurrent mutation hazards) -------------
    "racy_balance": textwrap.dedent("""\
        class SharedBalance:
            def __init__(self, initial=0):
                self.balance = initial

            def transfer(self, other, amount):
                if self.balance >= amount:
                    self.balance -= amount
                    other.balance += amount
                    return True
                return False

            def deposit(self, amount):
                temp = self.balance
                temp += amount
                self.balance = temp

            def withdraw(self, amount):
                temp = self.balance
                if temp >= amount:
                    temp -= amount
                    self.balance = temp
                    return True
                return False
    """),
    "racy_queue": textwrap.dedent("""\
        class SharedQueue:
            def __init__(self):
                self._items = []
                self._size = 0

            def enqueue(self, item):
                self._items.append(item)
                self._size += 1

            def dequeue(self):
                if self._size > 0:
                    item = self._items.pop(0)
                    self._size -= 1
                    return item
                raise IndexError("empty queue")

            def peek(self):
                if self._size > 0:
                    return self._items[0]
                return None

            def is_empty(self):
                return self._size == 0
    """),

    # -- Mixed IO (file/network + state) ---------------------------------------
    "mixed_logger": textwrap.dedent("""\
        import os, time

        class Logger:
            def __init__(self, path):
                self.path = path
                self.entries = []

            def log(self, level, message):
                entry = {'time': time.time(), 'level': level, 'msg': message}
                self.entries.append(entry)
                return entry

            def flush(self):
                with open(self.path, 'a') as f:
                    for e in self.entries:
                        f.write(str(e) + '\\n')
                self.entries.clear()

            def read_all(self):
                if os.path.exists(self.path):
                    with open(self.path) as f:
                        return f.readlines()
                return []
    """),
    "mixed_config": textwrap.dedent("""\
        import json, os

        class ConfigManager:
            def __init__(self, filepath):
                self.filepath = filepath
                self._data = {}
                self._dirty = False

            def load(self):
                if os.path.exists(self.filepath):
                    with open(self.filepath) as f:
                        self._data = json.load(f)
                self._dirty = False
                return self._data

            def get(self, key, default=None):
                return self._data.get(key, default)

            def set(self, key, value):
                self._data[key] = value
                self._dirty = True

            def save(self):
                if self._dirty:
                    with open(self.filepath, 'w') as f:
                        json.dump(self._data, f)
                    self._dirty = False
    """),
    "mixed_downloader": textwrap.dedent("""\
        import asyncio, os

        async def download_file(url, dest):
            await asyncio.sleep(0.01)
            data = 'simulated content from ' + url
            with open(dest, 'w') as f:
                f.write(data)
            return len(data)

        async def batch_download(urls, output_dir):
            os.makedirs(output_dir, exist_ok=True)
            results = {}
            for i, url in enumerate(urls):
                dest = os.path.join(output_dir, f'file_{i}.txt')
                size = await download_file(url, dest)
                results[url] = size
            return results
    """),
}

# Effect category classification (ground truth)
EFFECT_CATEGORIES = {
    "async_pipeline":   "pure_async",
    "async_generator":  "pure_async",
    "pure_coroutine":   "pure_async",
    "benign_counter":   "shared_benign",
    "benign_cache":     "shared_benign",
    "racy_balance":     "shared_races",
    "racy_queue":       "shared_races",
    "mixed_logger":     "mixed_io",
    "mixed_config":     "mixed_io",
    "mixed_downloader": "mixed_io",
}

CATEGORY_LABELS = {
    "pure_async":    "Pure async",
    "shared_benign": "Shared-state (benign)",
    "shared_races":  "Shared-state (races)",
    "mixed_io":      "Mixed IO",
}

CATEGORY_ORDER = ["pure_async", "shared_benign", "shared_races", "mixed_io"]


def main():
    print("=" * 60)
    print("Experiment 24 -- Async Effects: Effect Boundary Analysis")
    print("=" * 60)

    tmpfiles = []
    results = []

    for pname, source in PROGRAMS.items():
        path = write_temp_py(source)
        tmpfiles.append(path)

        t0 = time.time()
        load_objs = run_jugeo("load", path)
        descend_objs = run_jugeo("descend", path)
        bugs_objs = run_jugeo("bugs", path)
        elapsed_ms = (time.time() - t0) * 1000.0

        # Parse load
        coords = 0
        morphisms = 0
        if load_objs:
            s = load_objs[0].get("summary", load_objs[0])
            coords = s.get("coordinates", 0)
            morphisms = s.get("morphisms", 0)

        # Parse descend
        verdict = "unknown"
        local_sections = 0
        obstructions = 0
        props = 0
        if descend_objs:
            d = descend_objs[0]
            verdict = d.get("verdict", "unknown")
            local_sections = d.get("local_sections", 0)
            obs_list = d.get("obstructions", [])
            obstructions = len(obs_list) if isinstance(obs_list, list) else 0
            secs = d.get("sections_detail", [])
            props = sum(sec.get("propositions", 0) for sec in secs)

        # Parse bugs
        bug_count = 0
        if bugs_objs:
            b = bugs_objs[0] if isinstance(bugs_objs[0], dict) else {}
            bug_count = b.get("count", 0)

        cat = EFFECT_CATEGORIES[pname]
        results.append({
            "name": pname,
            "category": cat,
            "coords": coords,
            "morphisms": morphisms,
            "verdict": verdict,
            "local_sections": local_sections,
            "obstructions": obstructions,
            "props": props,
            "bug_count": bug_count,
            "elapsed_ms": elapsed_ms,
        })
        print("  {:<22} cat={:<18} coords={:>2}  obs={:>2}  bugs={:>2}  verdict={}".format(
            pname, cat, coords, obstructions, bug_count, verdict))

    # -- Per-category aggregation ----------------------------------------------
    cat_stats = {}
    for cat in CATEGORY_ORDER:
        cat_results = [r for r in results if r["category"] == cat]
        n = len(cat_results)
        if n == 0:
            cat_stats[cat] = {
                "count": 0, "accuracy": 0.0,
                "clean": 0, "dirty": 0, "false_pos": 0,
                "mean_time": 0.0,
            }
            continue

        # "clean" = verified with no obstructions; "dirty" = has obstructions
        clean = sum(1 for r in cat_results if r["obstructions"] == 0 and r["verdict"] == "verified")
        dirty = sum(1 for r in cat_results if r["obstructions"] > 0)
        # False positives: bugs detected in benign/pure categories
        if cat in ("pure_async", "shared_benign"):
            false_pos = sum(1 for r in cat_results if r["bug_count"] > 0)
        else:
            false_pos = 0

        # "accuracy" = fraction verified correctly (clean for benign, detected for racy)
        if cat in ("pure_async", "shared_benign"):
            accuracy = clean / n * 100.0
        else:
            accuracy = dirty / n * 100.0

        mean_time = statistics.mean([r["elapsed_ms"] for r in cat_results])

        cat_stats[cat] = {
            "count": n,
            "accuracy": accuracy,
            "clean": clean,
            "dirty": dirty,
            "false_pos": false_pos,
            "mean_time": mean_time,
        }

    # -- Overall stats ---------------------------------------------------------
    total_programs = len(results)
    total_props = sum(r["props"] for r in results)
    total_obstructions = sum(r["obstructions"] for r in results)
    total_bugs = sum(r["bug_count"] for r in results)
    overall_clean = sum(cs["clean"] for cs in cat_stats.values())
    overall_dirty = sum(cs["dirty"] for cs in cat_stats.values())
    overall_fp = sum(cs["false_pos"] for cs in cat_stats.values())
    overall_accuracy = sum(cs["accuracy"] * cs["count"] for cs in cat_stats.values()) / max(total_programs, 1)
    mean_time_all = statistics.mean([r["elapsed_ms"] for r in results]) if results else 0.0
    mean_coords = statistics.mean([r["coords"] for r in results]) if results else 0
    mean_morphisms = statistics.mean([r["morphisms"] for r in results]) if results else 0

    # -- Write macros ----------------------------------------------------------
    out_path = os.path.join(REPO_ROOT, "papers", "data-paper24.tex")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Macro name helper: category key → camelCase suffix
    cat_macro_suffix = {
        "pure_async":    "Pure",
        "shared_benign": "SharedBenign",
        "shared_races":  "SharedRaces",
        "mixed_io":      "MixedIo",
    }

    with open(out_path, "w") as f:
        f.write("% data-paper24.tex -- AUTO-GENERATED by exp24_async_effects.py\n")
        f.write("% DO NOT EDIT -- regenerate with: python3 experiments/exp24_async_effects.py\n\n")

        f.write("% --- Overall statistics ---\n")
        write_macro(f, "ppTwentyfourTotalPrograms", fmt_int(total_programs))
        write_macro(f, "ppTwentyfourTotalProps", fmt_int(total_props))
        write_macro(f, "ppTwentyfourTotalObstructions", fmt_int(total_obstructions))
        write_macro(f, "ppTwentyfourTotalBugs", fmt_int(total_bugs))
        write_macro(f, "ppTwentyfourOverallAccuracy", fmt_pct(overall_accuracy))
        write_macro(f, "ppTwentyfourOverallClean", fmt_int(overall_clean))
        write_macro(f, "ppTwentyfourOverallDirty", fmt_int(overall_dirty))
        write_macro(f, "ppTwentyfourOverallFalsePos", fmt_int(overall_fp))
        write_macro(f, "ppTwentyfourMeanTime", fmt_ms(mean_time_all))
        write_macro(f, "ppTwentyfourMeanCoords", "{:.1f}".format(mean_coords))
        write_macro(f, "ppTwentyfourMeanMorphisms", "{:.1f}".format(mean_morphisms))

        f.write("\n% --- Per-category: effect classification accuracy table ---\n")
        for cat in CATEGORY_ORDER:
            suffix = cat_macro_suffix[cat]
            cs = cat_stats[cat]
            write_macro(f, "ppTwentyfour{}Count".format(suffix), fmt_int(cs["count"]))
            write_macro(f, "ppTwentyfour{}Acc".format(suffix), fmt_pct(cs["accuracy"]))
            write_macro(f, "ppTwentyfour{}Clean".format(suffix), fmt_int(cs["clean"]))
            write_macro(f, "ppTwentyfour{}Dirty".format(suffix), fmt_int(cs["dirty"]))
            write_macro(f, "ppTwentyfour{}FalsePos".format(suffix), fmt_int(cs["false_pos"]))
            write_macro(f, "ppTwentyfour{}Time".format(suffix), fmt_ms(cs["mean_time"]))

        f.write("\n% --- Aliases for paper table placeholders ---\n")
        write_macro(f, "expAccuracy", fmt_pct(overall_accuracy))
        write_macro(f, "expPropsSum", fmt_int(total_props))
        write_macro(f, "expObstructionTotal", fmt_int(total_obstructions))

    print()
    print("Wrote " + out_path)
    print()
    print("SUMMARY:")
    print("  Total programs:      {}".format(total_programs))
    print("  Overall accuracy:    {:.1f}%".format(overall_accuracy))
    print("  Total propositions:  {}".format(total_props))
    print("  Total obstructions:  {}".format(total_obstructions))
    print("  Total bugs found:    {}".format(total_bugs))
    print("  Mean time per prog:  {:.2f} ms".format(mean_time_all))
    for cat in CATEGORY_ORDER:
        cs = cat_stats[cat]
        print("  {:<26} acc={:.1f}%  clean={}  dirty={}  FP={}".format(
            CATEGORY_LABELS[cat], cs["accuracy"], cs["clean"], cs["dirty"], cs["false_pos"]))

    # cleanup
    for p in tmpfiles:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()

# Also write results JSON
import json as _json
_results_path = os.path.join(os.path.dirname(__file__), "results_paper24.json")
with open(_results_path, "w") as _f:
    _json.dump({"paper": 24, "status": "completed"}, _f, indent=2)
print(f"Wrote {_results_path}")
