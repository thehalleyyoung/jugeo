"""Git-backed provenance tracking for generated research artifacts.

Every output directory produced by directed research becomes a **private
GitHub repository** whose commit history is the full audit trail of semantic
moves.  Each commit records:

- Which semantic move was executed (surface, coordinate, move kind)
- The trust level of the resulting section
- The current phase and how it maps to judgment-geometric descent
- A human-readable summary of what changed
- All new/modified code, data, and paper files

The repo is created on GitHub as a private repo via ``gh repo create``,
and every commit is pushed immediately so progress is continuously visible.

Commit messages follow a structured format that encodes the sheaf-theoretic
semantics of each move::

    [PHASE/SURFACE] coordinate (trust=LEVEL)

    {detailed summary}

    ─── Judgment Geometry Context ───
    Surface:    {surface} — the artifact kind (T/R/E/P)
    Coordinate: {coordinate} — position in the semantic site
    Trust:      {trust} ({label}) — evidence strength
    Phase:      {phase} — pipeline stage
    Descent:    {descent_context} — how this move serves convergence
    Files:      {n_files_changed}

Usage::

    tracker = OutputRepoTracker("/path/to/output", repo_name="my-project")
    tracker.create_github_repo()  # creates private repo + pushes
    tracker.commit_move(
        surface="CODE", coordinate="code.pricing_engine",
        trust=0.3, summary="Generated pricing engine (850 lines)",
        phase="GENERATE",
    )
    # commit is auto-pushed after creation
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jugeo.git_tracking")


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

def prompt_to_slug(prompt: str, max_len: int = 50) -> str:
    """Convert a natural-language prompt to a filesystem/repo-safe slug.

    'make me a killer app in finance using advanced math'
    → 'killer-app-finance-advanced-math'
    """
    # Drop common filler words
    stopwords = {"make", "me", "a", "an", "the", "in", "using", "with",
                 "for", "and", "of", "to", "that", "is", "it", "on",
                 "by", "from", "be", "do", "my", "i", "we", "you"}
    words = re.sub(r"[^a-zA-Z0-9\s]", "", prompt.lower()).split()
    words = [w for w in words if w not in stopwords and len(w) > 1]
    slug = "-".join(words[:8])
    return slug[:max_len] or "research-output"


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


def _descent_context(phase: str, surface: str, coordinate: str) -> str:
    """Generate judgment-geometry context for a commit message.

    Maps each (phase, surface) pair to its sheaf-theoretic meaning.
    """
    contexts = {
        ("IDEATION", "THEORY"): (
            "Cross-domain synthesis via H¹ of the Čech complex on the "
            "domain site.  This section is a candidate bridge proposition — "
            "a nonzero class in H¹(U∩V) that is useful at the problem point."
        ),
        ("SEED", "THEORY"): (
            "Constructing local sections on the Theory surface (T) of the "
            "4-surface workspace site.  Domain analysis produces the covering "
            "family; theory elaboration fills in sections over each patch."
        ),
        ("SEED", "CODE"): (
            "Architecture design: decomposing the implementation into a "
            "covering family of modules.  Each module is a patch in the cover, "
            "the sheaf condition requires their interfaces to agree on overlaps."
        ),
        ("GENERATE", "CODE"): (
            "Generating local sections on the Code surface (R).  Each module "
            "is a section over one patch.  The T→R morphism (implementation) "
            "must be compatible: code implements theory."
        ),
        ("GENERATE", "EVIDENCE"): (
            "Generating test suite: sections on the Evidence surface (E).  "
            "Tests witness the R→E morphism (code produces evidence)."
        ),
        ("HARDEN", "EVIDENCE"): (
            "Descent loop: running benchmarks to fill the Evidence surface.  "
            "Trust promotion from COPILOT_SUGGESTED (0.3) to RUNTIME_WITNESSED "
            "(0.7) requires actual execution against real data."
        ),
        ("HARDEN", "CODE"): (
            "Repair move: fixing code to satisfy the quality site.  "
            "The ConvergenceCriterion identified an obstruction; this commit "
            "applies the repair frontier's recommended semantic move."
        ),
        ("HARDEN", "CLAIMS"): (
            "Grounding claims against evidence (E→P overlap check).  "
            "Every numerical claim in the paper must restrict to a matching "
            "value on the Evidence surface — the sheaf condition on E∩P."
        ),
        ("TAIL", "CLAIMS"): (
            "Generating paper and README: sections on the Claims surface (P).  "
            "The paper is the global section on P; its trust is bounded by the "
            "minimum trust of contributing evidence (trust floor gating)."
        ),
        ("TAIL", "EVIDENCE"): (
            "Final verification: descent on the full workspace site to check "
            "H¹ = 0.  All four surfaces must be consistent on their overlaps."
        ),
    }
    key = (phase.upper(), surface.upper())
    if key in contexts:
        return contexts[key]
    return (
        f"Semantic move on {surface} surface at {coordinate} during "
        f"{phase} phase.  This section contributes to descent on the "
        f"workspace site; convergence requires all overlaps to agree."
    )


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
    pushed: bool = False


@dataclass
class OutputRepoTracker:
    """Manages a git+GitHub repository inside a research output directory.

    Creates a private GitHub repo and pushes every commit so that progress
    is continuously visible.  All operations are best-effort: git/gh
    failures are logged but never raise exceptions.
    """
    output_dir: str
    repo_name: str = ""
    commits: list[MoveCommit] = field(default_factory=list)
    _initialized: bool = field(default=False, repr=False)
    _git_available: bool = field(default=True, repr=False)
    _gh_available: bool = field(default=True, repr=False)
    _remote_set: bool = field(default=False, repr=False)
    _github_url: str = field(default="", repr=False)
    auto_push: bool = True

    def __post_init__(self):
        try:
            subprocess.run(["git", "--version"],
                           capture_output=True, timeout=5)
        except Exception:
            self._git_available = False
            logger.warning("git not found — output repo tracking disabled")

        try:
            subprocess.run(["gh", "--version"],
                           capture_output=True, timeout=5)
        except Exception:
            self._gh_available = False
            logger.info("gh CLI not found — GitHub push disabled")

    # ------------------------------------------------------------------
    #  Repository initialization
    # ------------------------------------------------------------------

    def _ensure_repo(self) -> bool:
        """Initialize the local git repo if it doesn't exist yet."""
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

            gitignore = repo_dir / ".gitignore"
            gitignore.write_text(
                "__pycache__/\n*.pyc\n*.egg-info/\n.mypy_cache/\n"
                ".pytest_cache/\n*.aux\n*.log\n*.out\n*.synctex.gz\n"
                "*.fls\n*.fdb_latexmk\n.hypothesis/\ndist/\nbuild/\n"
                ".venv/\nnode_modules/\n"
            )

            self._run_git("add", "-A")
            self._run_git(
                "commit", "-m",
                f"[INIT] Research output repository: {self.repo_name}\n\n"
                f"Auto-created by jugeo directed research.\n"
                f"This repository tracks every semantic move in the\n"
                f"judgment-geometric research pipeline.\n\n"
                f"Each commit = one move on the 4-surface workspace site\n"
                f"(Theory, Code, Evidence, Claims).\n\n"
                f"Convergence = H¹ = 0 on all overlaps.\n\n"
                f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                "--allow-empty",
            )
            self._initialized = True
            logger.info("Initialized git repo at %s", self.output_dir)
        except Exception as e:
            logger.warning("Failed to initialize git repo: %s", e)
            self._git_available = False

        return self._initialized

    def create_github_repo(self, description: str = "") -> Optional[str]:
        """Create a private GitHub repo and set it as origin.

        Uses ``gh repo create`` to create a private repo.  Returns the
        GitHub URL on success, None on failure.
        """
        if not self._gh_available or not self._ensure_repo():
            return None

        if self._remote_set:
            return self._github_url

        name = self.repo_name or "research-output"
        desc = description or f"JuGeo research: {name}"

        try:
            result = subprocess.run(
                ["gh", "repo", "create", name,
                 "--private",
                 "--description", desc[:350],
                 "--source", self.output_dir,
                 "--push"],
                capture_output=True, text=True, timeout=60,
                cwd=self.output_dir,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode == 0:
                # Extract URL from output
                url = result.stdout.strip()
                if not url:
                    url = f"https://github.com/{self._get_gh_user()}/{name}"
                self._github_url = url
                self._remote_set = True
                logger.info("Created GitHub repo: %s", url)
                return url
            else:
                # Repo might already exist — try to add remote
                logger.info("gh repo create failed (%s), trying to add remote",
                            result.stderr[:100])
                return self._try_add_existing_remote(name)
        except Exception as e:
            logger.warning("GitHub repo creation failed: %s", e)
            return None

    def _try_add_existing_remote(self, name: str) -> Optional[str]:
        """Try to add an existing GitHub repo as origin."""
        user = self._get_gh_user()
        if not user:
            return None
        url = f"https://github.com/{user}/{name}.git"
        result = self._run_git("remote", "add", "origin", url)
        if result is not None:
            self._remote_set = True
            self._github_url = url
            self._push()
            return url
        # Remote might already exist
        existing = self._run_git("remote", "get-url", "origin")
        if existing:
            self._remote_set = True
            self._github_url = existing.strip()
            self._push()
            return self._github_url
        return None

    def _get_gh_user(self) -> str:
        """Get the current GitHub username via gh."""
        try:
            r = subprocess.run(
                ["gh", "api", "user", "-q", ".login"],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _push(self) -> bool:
        """Push current branch to origin."""
        if not self._remote_set:
            return False
        result = self._run_git("push", "-u", "origin", "HEAD")
        return result is not None

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
        """Stage all changes and commit with a detailed JG-theoretic message.

        The commit message includes: what changed, what phase, what surface,
        how this move relates to descent convergence on the workspace site,
        and the trust level of the evidence.

        If auto_push is True and a remote is configured, pushes immediately.
        """
        if not self._ensure_repo():
            return None

        self._run_git("add", "-A")

        status = self._run_git("status", "--porcelain")
        if status is not None and not status.strip():
            return None

        diff_stat = self._run_git("diff", "--cached", "--numstat") or ""
        n_files = len([l for l in diff_stat.strip().splitlines() if l.strip()])

        # Build detailed commit message
        trust_lbl = _trust_label(trust)
        descent_ctx = _descent_context(phase, surface, coordinate)

        title = f"[{phase}/{surface}] {coordinate} (trust={trust_lbl})"
        body = "\n".join([
            "",
            summary,
            "",
            "─── Judgment Geometry Context ───",
            f"Surface:    {surface} — artifact kind in workspace site",
            f"Coordinate: {coordinate} — position in semantic site",
            f"Trust:      {trust:.2f} ({trust_lbl})",
            f"Phase:      {phase}",
            f"Descent:    {descent_ctx}",
            f"Files:      {n_files}",
            f"Timestamp:  {time.strftime('%Y-%m-%d %H:%M:%S')}",
        ])

        if extra_metadata:
            body += "\n\nMetadata:"
            for k, v in extra_metadata.items():
                body += f"\n  {k}: {v}"

        message = title + body

        result = self._run_git("commit", "-m", message)
        if result is None:
            return None

        sha = (self._run_git("rev-parse", "--short", "HEAD") or "?").strip()

        pushed = False
        if self.auto_push and self._remote_set:
            pushed = self._push()

        commit = MoveCommit(
            sha=sha, surface=surface, coordinate=coordinate,
            trust=trust, summary=summary, phase=phase,
            timestamp=time.time(), files_changed=n_files,
            pushed=pushed,
        )
        self.commits.append(commit)
        logger.info(
            "Committed [%s] %s/%s (%d files)%s",
            sha, surface, coordinate, n_files,
            " → pushed" if pushed else "",
        )
        return commit

    # ------------------------------------------------------------------
    #  Phase boundary commits
    # ------------------------------------------------------------------

    def commit_phase_boundary(
        self, phase: str, summary: str = "",
    ) -> Optional[MoveCommit]:
        """Commit a phase transition with descent context."""
        phase_descs = {
            "IDEATION": (
                "Entering IDEATION: constructing the domain site and "
                "searching H¹ of the Čech complex for novel bridge "
                "propositions via cross-domain synthesis."
            ),
            "SEED": (
                "Entering SEED: domain analysis produces the covering family "
                "on the Theory surface; theory elaboration fills sections."
            ),
            "GENERATE": (
                "Entering GENERATE: architecture decomposes into a cover "
                "of modules (patches); code generation fills each patch "
                "with local sections on the Code surface."
            ),
            "HARDEN": (
                "Entering HARDEN: the descent loop.  Running benchmarks, "
                "checking the quality site, repairing obstructions until "
                "H¹ = 0 and all trust floors are met."
            ),
            "TAIL": (
                "Entering TAIL: generating paper (Claims surface) and "
                "running final descent to verify workspace consistency. "
                "The paper is the global section on P."
            ),
        }
        desc = phase_descs.get(phase.upper(), summary or f"Phase: {phase}")
        return self.commit_move(
            surface="LIFECYCLE",
            coordinate=f"phase.{phase.lower()}",
            trust=1.0,
            summary=desc,
            phase=phase,
        )

    # ------------------------------------------------------------------
    #  Documentation commits
    # ------------------------------------------------------------------

    def commit_documentation(
        self, doc_type: str, content_summary: str, phase: str = "",
    ) -> Optional[MoveCommit]:
        """Commit a documentation update (README, CHANGELOG, etc.)."""
        return self.commit_move(
            surface="CLAIMS",
            coordinate=f"docs.{doc_type}",
            trust=TRUST_COPILOT,
            summary=f"Documentation update ({doc_type}): {content_summary}",
            phase=phase or self._infer_phase(),
        )

    def _infer_phase(self) -> str:
        """Infer current phase from commit history."""
        for c in reversed(self.commits):
            if c.surface == "LIFECYCLE":
                return c.phase
        return "UNKNOWN"

    # ------------------------------------------------------------------
    #  Summary and persistence
    # ------------------------------------------------------------------

    def log_summary(self) -> str:
        """Human-readable summary of all commits."""
        if not self.commits:
            return "No commits recorded."
        lines = [f"Git history ({len(self.commits)} commits):"]
        for c in self.commits:
            push_marker = " ↑" if c.pushed else ""
            lines.append(
                f"  {c.sha} [{c.phase}/{c.surface}] {c.coordinate} "
                f"({_trust_label(c.trust)}, {c.files_changed} files){push_marker}"
            )
        if self._github_url:
            lines.append(f"  GitHub: {self._github_url}")
        return "\n".join(lines)

    def save_commit_log(self) -> None:
        """Write the commit log as JSON to the output directory."""
        path = Path(self.output_dir) / "commit_log.json"
        data = [
            {
                "sha": c.sha,
                "surface": c.surface,
                "coordinate": c.coordinate,
                "trust": c.trust,
                "summary": c.summary,
                "phase": c.phase,
                "timestamp": c.timestamp,
                "files_changed": c.files_changed,
                "pushed": c.pushed,
            }
            for c in self.commits
        ]
        path.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    #  Internal git runner
    # ------------------------------------------------------------------

    def _run_git(self, *args: str) -> Optional[str]:
        """Run a git command in the output directory."""
        try:
            r = subprocess.run(
                ["git"] + list(args),
                capture_output=True, text=True,
                cwd=self.output_dir, timeout=30,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode == 0:
                return r.stdout
            logger.debug("git %s failed: %s", args[0], r.stderr[:200])
            return None
        except Exception as e:
            logger.debug("git %s exception: %s", args[0], e)
            return None


# For use in commit_documentation
TRUST_COPILOT = 0.3
