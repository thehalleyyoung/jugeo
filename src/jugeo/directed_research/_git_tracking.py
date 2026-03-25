"""Git-backed provenance tracking for generated research artifacts.

Every output directory produced by directed research becomes a **private
git repository** whose commit history is the full audit trail of semantic
moves.  Each commit records:

- Which semantic move was executed (surface, coordinate, move kind)
- The trust level of the resulting section
- A human-readable summary of what changed
- All new/modified code, data, and paper files

This is domain-agnostic infrastructure — it works for any directed research
run, not just a specific project.

The git repo is initialized lazily on the first ``commit_move`` call.
Subsequent calls do ``git add -A && git commit``.  The commit message
follows a structured format::

    [MOVE] {surface}/{coordinate} (trust={trust})

    {summary}

    Surface:    {surface}
    Coordinate: {coordinate}
    Trust:      {trust}
    Phase:      {phase}
    Files:      {n_files_changed}

Usage::

    from jugeo.directed_research._git_tracking import OutputRepoTracker

    tracker = OutputRepoTracker("/path/to/output/dir")
    tracker.commit_move(
        surface="CODE",
        coordinate="code.pricing_engine",
        trust=0.3,
        summary="Generated pricing engine module (850 lines)",
        phase="GENERATE",
    )
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jugeo.git_tracking")


@dataclass
class MoveCommit:
    """Record of a single committed semantic move."""
    sha: str
    surface: str
    coordinate: str
    trust: float
    summary: str
    phase: str
    timestamp: float
    files_changed: int


@dataclass
class OutputRepoTracker:
    """Manages a git repository inside a research output directory.

    Initialized lazily — the repo is created on the first commit.
    All operations are best-effort: git failures are logged but never
    raise exceptions (research must not be blocked by VCS issues).
    """
    output_dir: str
    commits: list[MoveCommit] = field(default_factory=list)
    _initialized: bool = field(default=False, repr=False)
    _git_available: bool = field(default=True, repr=False)

    def __post_init__(self):
        # Check git is available once
        try:
            subprocess.run(
                ["git", "--version"],
                capture_output=True, timeout=5,
            )
        except Exception:
            self._git_available = False
            logger.warning("git not found — output repo tracking disabled")

    # ------------------------------------------------------------------
    #  Initialization
    # ------------------------------------------------------------------

    def _ensure_repo(self) -> bool:
        """Initialize the git repo if it doesn't exist yet."""
        if self._initialized or not self._git_available:
            return self._initialized

        repo_dir = Path(self.output_dir)
        git_dir = repo_dir / ".git"

        if git_dir.exists():
            self._initialized = True
            return True

        try:
            repo_dir.mkdir(parents=True, exist_ok=True)
            self._run_git("init")
            # Write .gitignore
            gitignore = repo_dir / ".gitignore"
            gitignore.write_text(
                "__pycache__/\n*.pyc\n*.egg-info/\n.mypy_cache/\n"
                ".pytest_cache/\n*.aux\n*.log\n*.out\n*.synctex.gz\n"
                "*.fls\n*.fdb_latexmk\n.hypothesis/\n"
            )
            # Initial commit
            self._run_git("add", "-A")
            self._run_git(
                "commit", "-m",
                "[INIT] Research output repository\n\n"
                "Auto-created by jugeo directed research.\n"
                f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                "--allow-empty",
            )
            self._initialized = True
            logger.info("Initialized git repo at %s", self.output_dir)
        except Exception as e:
            logger.warning("Failed to initialize git repo: %s", e)
            self._git_available = False

        return self._initialized

    # ------------------------------------------------------------------
    #  Committing semantic moves
    # ------------------------------------------------------------------

    def commit_move(
        self,
        *,
        surface: str,
        coordinate: str,
        trust: float,
        summary: str,
        phase: str = "",
        extra_metadata: dict[str, Any] | None = None,
    ) -> Optional[MoveCommit]:
        """Stage all changes and commit with a structured move message.

        Returns the MoveCommit record, or None if the commit failed
        or there was nothing to commit.
        """
        if not self._ensure_repo():
            return None

        # Stage everything
        self._run_git("add", "-A")

        # Check if there's anything to commit
        status = self._run_git("status", "--porcelain")
        if status is not None and not status.strip():
            logger.debug("No changes to commit for %s/%s", surface, coordinate)
            return None

        # Count files changed
        diff_stat = self._run_git("diff", "--cached", "--numstat") or ""
        n_files = len([l for l in diff_stat.strip().splitlines() if l.strip()])

        # Build commit message
        trust_label = _trust_label(trust)
        title = f"[{surface}] {coordinate} (trust={trust_label})"
        body_lines = [
            "",
            summary,
            "",
            f"Surface:    {surface}",
            f"Coordinate: {coordinate}",
            f"Trust:      {trust:.2f} ({trust_label})",
        ]
        if phase:
            body_lines.append(f"Phase:      {phase}")
        body_lines.append(f"Files:      {n_files}")
        body_lines.append(f"Timestamp:  {time.strftime('%Y-%m-%d %H:%M:%S')}")

        if extra_metadata:
            body_lines.append("")
            body_lines.append("Metadata:")
            for k, v in extra_metadata.items():
                body_lines.append(f"  {k}: {v}")

        message = title + "\n" + "\n".join(body_lines)

        # Commit
        result = self._run_git("commit", "-m", message)
        if result is None:
            return None

        # Get the SHA
        sha = (self._run_git("rev-parse", "--short", "HEAD") or "unknown").strip()

        commit = MoveCommit(
            sha=sha,
            surface=surface,
            coordinate=coordinate,
            trust=trust,
            summary=summary,
            phase=phase,
            timestamp=time.time(),
            files_changed=n_files,
        )
        self.commits.append(commit)
        logger.info(
            "Committed [%s] %s/%s (%d files) → %s",
            sha, surface, coordinate, n_files, trust_label,
        )
        return commit

    # ------------------------------------------------------------------
    #  Convenience: commit a phase boundary
    # ------------------------------------------------------------------

    def commit_phase_boundary(self, phase: str, summary: str = "") -> Optional[MoveCommit]:
        """Commit a phase transition marker (e.g. SEED → GENERATE)."""
        return self.commit_move(
            surface="LIFECYCLE",
            coordinate=f"phase.{phase.lower()}",
            trust=1.0,
            summary=summary or f"Entering phase: {phase}",
            phase=phase,
        )

    # ------------------------------------------------------------------
    #  Log summary
    # ------------------------------------------------------------------

    def log_summary(self) -> str:
        """Return a human-readable summary of all commits."""
        if not self.commits:
            return "No commits recorded."
        lines = [f"Git history ({len(self.commits)} commits):"]
        for c in self.commits:
            lines.append(
                f"  {c.sha} [{c.surface}] {c.coordinate} "
                f"({_trust_label(c.trust)}, {c.files_changed} files)"
            )
        return "\n".join(lines)

    def save_commit_log(self) -> None:
        """Write the commit log as JSON to the output directory."""
        path = Path(self.output_dir) / "commit_log.json"
        path.write_text(json.dumps(
            [
                {
                    "sha": c.sha,
                    "surface": c.surface,
                    "coordinate": c.coordinate,
                    "trust": c.trust,
                    "summary": c.summary,
                    "phase": c.phase,
                    "timestamp": c.timestamp,
                    "files_changed": c.files_changed,
                }
                for c in self.commits
            ],
            indent=2,
        ))

    # ------------------------------------------------------------------
    #  Internal git runner
    # ------------------------------------------------------------------

    def _run_git(self, *args: str) -> Optional[str]:
        """Run a git command in the output directory.

        Returns stdout on success, None on failure.
        """
        try:
            r = subprocess.run(
                ["git"] + list(args),
                capture_output=True,
                text=True,
                cwd=self.output_dir,
                timeout=30,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode == 0:
                return r.stdout
            logger.debug("git %s failed: %s", args[0], r.stderr[:200])
            return None
        except Exception as e:
            logger.debug("git %s exception: %s", args[0], e)
            return None


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

def _trust_label(trust: float) -> str:
    """Human-readable trust level label."""
    if trust >= 0.9:
        return "SOLVER_DISCHARGED"
    elif trust >= 0.7:
        return "RUNTIME_WITNESSED"
    elif trust >= 0.3:
        return "COPILOT_SUGGESTED"
    else:
        return "UNVERIFIED"
