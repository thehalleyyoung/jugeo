#!/usr/bin/env python3
"""Drive local copilot over theory2-src-blueprint.json one file at a time.

This script uses Copilot's non-interactive prompt mode (``copilot -p``) with
auto-approval flags because the local CLI expects prompt mode rather than raw
positional prompting.
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_BLUEPRINT = "theory2-src-blueprint.json"
DEFAULT_ORDER_FILE = "theory2-generation-order.json"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_BINARY = "copilot"
DEFAULT_STATE = ".copilot-generation-state.json"
MIN_PYTHON_FILE_BYTES = (15 * 1024) + 1
MAX_PYTHON_FILE_BYTES = 100 * 1024


@dataclass(frozen=True)
class WorkItem:
    index: int
    scope: str
    package_path: str
    target_relpath: str
    test_relpath: str
    title: str
    role: str
    estimated_loc: int
    classes: list[str]
    functions: list[str]
    chapter_number: int | None
    part_number: int | None
    source_sections: list[str]
    section_indexes: list[int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Traverse theory2-src-blueprint.json and ask copilot to generate "
            "each source file plus a matching test file."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument(
        "--blueprint",
        default=DEFAULT_BLUEPRINT,
        help="Path to the blueprint JSON relative to repo root.",
    )
    parser.add_argument(
        "--order-file",
        default=DEFAULT_ORDER_FILE,
        help=(
            "Optional JSON file describing the preferred dependency-aware generation "
            "order. Falls back to raw blueprint traversal when missing."
        ),
    )
    parser.add_argument(
        "--binary",
        default=os.environ.get("COPILOT_CLI_BINARY", DEFAULT_BINARY),
        help="copilot executable to invoke.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model name passed to copilot-cli.",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE,
        help="JSON file used to track completed work items.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Maximum number of pending files to generate this run. 0 means no limit.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="1-based traversal index to start from.",
    )
    parser.add_argument(
        "--only-path",
        action="append",
        default=[],
        help="Generate only the specified source relpath(s). May be given multiple times.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands and prompts without invoking copilot-cli.",
    )
    parser.add_argument(
        "--prompt-via-stdin",
        action="store_true",
        default=True,
        help="Compatibility flag; copilot prompt mode is used either way.",
    )
    parser.add_argument(
        "--prompt-as-arg",
        action="store_false",
        dest="prompt_via_stdin",
        help="Compatibility flag; copilot prompt mode is used either way.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep traversing after failures.",
    )
    parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Do not run pytest verification even if pytest is available.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"completed": {}, "failures": {}}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def test_relpath_for(target_relpath: str) -> str:
    target = Path(target_relpath)
    if target.parts and target.parts[0] == "src":
        rel = Path(*target.parts[1:])
    else:
        rel = target
    return str(Path("tests", *rel.parts[:-1], f"test_{target.stem}.py"))


def order_items(
    items: list[WorkItem],
    order_spec: dict[str, Any] | None,
) -> tuple[list[WorkItem], dict[str, dict[str, Any]]]:
    if not order_spec:
        return items, {}

    by_target = {item.target_relpath: item for item in items}
    ordered: list[WorkItem] = []
    order_meta: dict[str, dict[str, Any]] = {}

    for entry in order_spec.get("items", []):
        target = entry.get("target")
        if target in by_target:
            ordered.append(by_target[target])
            order_meta[target] = entry

    seen = {item.target_relpath for item in ordered}
    ordered.extend(item for item in items if item.target_relpath not in seen)
    return ordered, order_meta


def iter_work_items(blueprint: dict[str, Any]) -> Iterable[WorkItem]:
    index = 1
    root = blueprint["implementationTarget"]["root"]
    global_conventions = blueprint["implementationTarget"].get("globalConventions", [])

    for entry in blueprint.get("rootFiles", []):
        relpath = str(Path(root, entry["file"]))
        yield WorkItem(
            index=index,
            scope="root",
            package_path=root,
            target_relpath=relpath,
            test_relpath=test_relpath_for(relpath),
            title=entry["file"],
            role=entry.get("role", ""),
            estimated_loc=entry.get("estimatedLoC", 0),
            classes=list(entry.get("classes", [])),
            functions=list(entry.get("functions", [])),
            chapter_number=None,
            part_number=None,
            source_sections=list(global_conventions),
            section_indexes=[],
        )
        index += 1

    for directory in blueprint.get("sharedDirectories", []):
        package_path = directory["path"]
        source_sections = [directory.get("purpose", "")]
        for entry in directory.get("files", []):
            relpath = str(Path(package_path, entry["file"]))
            yield WorkItem(
                index=index,
                scope="shared",
                package_path=package_path,
                target_relpath=relpath,
                test_relpath=test_relpath_for(relpath),
                title=entry["file"],
                role=entry.get("role", ""),
                estimated_loc=entry.get("estimatedLoC", 0),
                classes=list(entry.get("classes", [])),
                functions=list(entry.get("functions", [])),
                chapter_number=None,
                part_number=None,
                source_sections=source_sections,
                section_indexes=[],
            )
            index += 1

    for chapter in blueprint.get("chapterDirectories", []):
        package_path = chapter["path"]
        chapter_title = chapter["title"]
        source_sections = list(chapter.get("sourceSections", []))
        for entry in chapter.get("files", []):
            relpath = str(Path(package_path, entry["file"]))
            yield WorkItem(
                index=index,
                scope="chapter",
                package_path=package_path,
                target_relpath=relpath,
                test_relpath=test_relpath_for(relpath),
                title=chapter_title,
                role=entry.get("role", ""),
                estimated_loc=entry.get("estimatedLoC", 0),
                classes=list(entry.get("classes", [])),
                functions=list(entry.get("functions", [])),
                chapter_number=chapter.get("chapterNumber"),
                part_number=chapter.get("partNumber"),
                source_sections=source_sections,
                section_indexes=list(entry.get("sectionIndexes", [])),
            )
            index += 1


def collect_existing_context(
    repo_root: Path,
    target_relpath: str,
    test_relpath: str,
    limit: int = 24,
) -> list[str]:
    target = Path(target_relpath)
    existing: list[tuple[int, str]] = []
    for candidate in list((repo_root / "src").rglob("*.py")) + list((repo_root / "tests").rglob("*.py")):
        rel = candidate.relative_to(repo_root).as_posix()
        if rel in {target_relpath, test_relpath}:
            continue
        score = 0
        candidate_parts = Path(rel).parts
        target_parts = target.parts
        common = 0
        for left, right in zip(candidate_parts, target_parts):
            if left != right:
                break
            common += 1
        score -= common * 10
        score += len(candidate_parts)
        existing.append((score, rel))
    existing.sort()
    return [rel for _, rel in existing[:limit]]


def build_prompt(
    blueprint: dict[str, Any],
    item: WorkItem,
    existing_files: list[str],
    order_entry: dict[str, Any] | None,
) -> str:
    lines = [
        "Generate exactly two files for this turn:",
        f"1. `{item.target_relpath}`",
        f"2. `{item.test_relpath}`",
        "",
        "Use the repository files `theory2-src-blueprint.json`, `theory2-generation-order.json`, `preliminaries/theory2.tex`, and `preliminaries/theory2.pdf` as the governing specification.",
        "",
        "Primary goals:",
        "- Maximize usability for human developers.",
        "- Maximize compatibility with theory2.tex's worldview.",
        "- Maximize compatibility with already generated project files and with future files implied by the blueprint.",
        "- Make the source file easy for a human or LLM to parse: explicit types, clear public API, readable structure, minimal hidden control flow, and stable data shapes.",
        "- Make both generated Python files larger than 15 KB and no larger than 100 KB on disk.",
        "- Write a matching test file in `tests/` that verifies this file against existing files and against the project's JuGeo goals.",
        "- You may import any library.",
        "- You can assume the generated source files themselves may include the text `copilot`, because these modules are allowed to orchestrate or call LLM/agent functionality and we are using copilot-cli as both.",
        "",
        "Source file requirements:",
        "- Prefer production-quality Python 3 code with strong typing and modularity.",
        "- Preserve explicit provenance, trust, and semantic boundary information where relevant.",
        "- Avoid placeholder-only implementations; provide real logic, seams, or well-typed adapters.",
        "- Do not rewrite unrelated files.",
        "",
        "Test file requirements:",
        "- Prefer `pytest` style tests.",
        "- Verify compatibility with any existing nearby modules.",
        "- If the wider dependency graph is not built yet, write seam-friendly tests around contracts, parsing behavior, imports, stable shapes, adapters, and intended JuGeo semantics.",
        "- Keep the tests honest about what is implemented now versus what remains to be integrated later.",
        "",
        "Current blueprint entry:",
        f"- Scope: {item.scope}",
        f"- Package path: `{item.package_path}`",
        f"- Target file: `{item.target_relpath}`",
        f"- Target test: `{item.test_relpath}`",
        f"- Estimated lines from blueprint: {item.estimated_loc}",
        f"- Role: {item.role or 'See blueprint context.'}",
        f"- Important classes to include or honor: {', '.join(item.classes) if item.classes else 'None listed.'}",
        f"- Important functions to include or honor: {', '.join(item.functions) if item.functions else 'None listed.'}",
    ]
    if item.chapter_number is not None:
        lines.extend(
            [
                f"- Chapter number: {item.chapter_number}",
                f"- Part number: {item.part_number}",
                f"- Chapter title: {item.title}",
            ]
        )
        if item.section_indexes:
            covered = [
                f"{idx}. {item.source_sections[idx - 1]}"
                for idx in item.section_indexes
                if 0 < idx <= len(item.source_sections)
            ]
            lines.append("- Covered source sections:")
            lines.extend(f"  - {section}" for section in covered)
        else:
            lines.append("- Source sections:")
            lines.extend(f"  - {section}" for section in item.source_sections)
    else:
        lines.append("- Governing context:")
        lines.extend(f"  - {section}" for section in item.source_sections)

    if existing_files:
        lines.append("- Existing nearby source/test files to stay compatible with:")
        lines.extend(f"  - `{path}`" for path in existing_files)
    else:
        lines.append("- Existing nearby source/test files to stay compatible with: none yet.")

    if order_entry:
        lines.append("- Generation-order metadata:")
        lines.append(f"  - Sequence: {order_entry.get('sequence')}")
        lines.append(f"  - Stage: {order_entry.get('stage')}")
        if order_entry.get("dependsOn"):
            lines.append("  - Required already-generated dependencies:")
            lines.extend(f"    - `{path}`" for path in order_entry["dependsOn"])
        if order_entry.get("why"):
            lines.append(f"  - Why now: {order_entry['why']}")

    lines.extend(
        [
            "",
            "When you are done, ensure both files are written to disk and that the source file is substantial, coherent, and ready for later files in traversal order.",
        ]
    )
    return "\n".join(lines)


def ensure_binary(binary: str) -> None:
    if shutil.which(binary):
        return
    raise FileNotFoundError(
        f"Could not find `{binary}` on PATH. Set --binary or COPILOT_CLI_BINARY."
    )


def run_copilot(
    binary: str,
    model: str,
    repo_root: Path,
    prompt: str,
    prompt_via_stdin: bool,
) -> subprocess.CompletedProcess[str]:
    del prompt_via_stdin
    cmd = [
        binary,
        "-p",
        prompt,
        "--autopilot",
        "--model",
        model,
        "--allow-all",
        "--no-ask-user",
    ]
    return subprocess.run(
        cmd,
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def verify_python_file_size(path: Path) -> None:
    size = path.stat().st_size
    if size < MIN_PYTHON_FILE_BYTES or size > MAX_PYTHON_FILE_BYTES:
        raise RuntimeError(
            f"Python file {path} is {size} bytes; expected more than 15 KB and at most 100 KB."
        )


def verify_source_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Expected generated source file at {path}")
    verify_python_file_size(path)
    text = path.read_text(errors="ignore")
    if "copilot" not in text.lower():
        raise RuntimeError(f"Source file {path} does not contain the token `copilot`.")
    if path.suffix == ".py":
        py_compile.compile(str(path), doraise=True)


def verify_test_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Expected generated test file at {path}")
    if path.suffix == ".py":
        verify_python_file_size(path)
        py_compile.compile(str(path), doraise=True)


def run_pytest(repo_root: Path, test_path: Path) -> None:
    if shutil.which("pytest") is None:
        return
    result = subprocess.run(
        ["pytest", str(test_path), "-q"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "pytest verification failed for "
            f"{test_path}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def select_items(items: list[WorkItem], args: argparse.Namespace) -> list[WorkItem]:
    selected = list(items)
    if args.start_index > 1:
        selected = selected[args.start_index - 1 :]
    if args.only_path:
        allowed = set(args.only_path)
        selected = [item for item in selected if item.target_relpath in allowed]
    if args.max_files > 0:
        selected = selected[: args.max_files]
    return selected


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    blueprint_path = (repo_root / args.blueprint).resolve()
    order_path = (repo_root / args.order_file).resolve()
    state_path = (repo_root / args.state_file).resolve()
    if not args.dry_run:
        ensure_binary(args.binary)

    blueprint = load_json(blueprint_path)
    order_spec = load_json(order_path) if order_path.exists() else None
    items, order_meta = order_items(list(iter_work_items(blueprint)), order_spec)
    pending = select_items(items, args)
    state = load_state(state_path)

    for item in pending:
        display_index = order_meta.get(item.target_relpath, {}).get("sequence", item.index)
        target_path = repo_root / item.target_relpath
        test_path = repo_root / item.test_relpath
        if os.path.exists(target_path):
            continue
        try:
            if target_path.exists() and test_path.exists():
                verify_source_file(target_path)
                verify_test_file(test_path)
                if not args.skip_pytest:
                    run_pytest(repo_root, test_path)
                state["completed"][item.target_relpath] = {
                    "index": display_index,
                    "status": "verified-existing",
                }
                save_state(state_path, state)
                print(f"[skip] #{display_index} {item.target_relpath} already exists and verifies.")
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"[regen] {item.target_relpath}: existing files failed verification: {exc}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.parent.mkdir(parents=True, exist_ok=True)
        existing_files = collect_existing_context(repo_root, item.target_relpath, item.test_relpath)
        prompt = build_prompt(
            blueprint,
            item,
            existing_files,
            order_meta.get(item.target_relpath),
        )

        if args.dry_run:
            print(f"\n=== WORK ITEM {display_index}: {item.target_relpath} ===")
            print(prompt)
            continue

        result = run_copilot(
            binary=args.binary,
            model=args.model,
            repo_root=repo_root,
            prompt=prompt,
            prompt_via_stdin=args.prompt_via_stdin,
        )

        if result.returncode != 0:
            state["failures"][item.target_relpath] = {
                "index": display_index,
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            }
            save_state(state_path, state)
            message = (
                f"copilot-cli failed for {item.target_relpath}\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
            if args.continue_on_error:
                print(f"[error] {message}", file=sys.stderr)
                continue
            raise RuntimeError(message)

        verify_source_file(target_path)
        verify_test_file(test_path)
        if not args.skip_pytest:
            run_pytest(repo_root, test_path)

        state["completed"][item.target_relpath] = {
            "index": display_index,
            "status": "generated-and-verified",
            "test": item.test_relpath,
        }
        state["failures"].pop(item.target_relpath, None)
        save_state(state_path, state)
        print(f"[ok] #{display_index} {item.target_relpath} -> {item.test_relpath}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
