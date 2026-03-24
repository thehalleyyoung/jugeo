"""Academic paper generation with jg trust-gated claims.

This module generates a conference paper as a section on the Claims surface.
The key invariant — enforced by the trust algebra — is:

    **A claim may only appear in the paper at a section appropriate to
    its trust level.** Specifically:

    Trust level              Allowed paper sections
    ─────────────────────────────────────────────────────────
    MECHANICALLY_VERIFIED    Theorem, Proposition, Lemma
    SOLVER_DISCHARGED        Results table, Algorithm correctness
    RUNTIME_WITNESSED        Evaluation section, Benchmarks table
    COPILOT_SUGGESTED        Discussion, Future Work, Conjecture
    UNVERIFIED               Nowhere — must be upgraded first

This means:
- A benchmark number can appear in §7 (Evaluation) ONLY if it was actually
  computed (trust >= RUNTIME_WITNESSED = 0.7).
- A theoretical claim can appear in §4 (Framework) as a Theorem ONLY if
  jg prove verified it (trust >= SOLVER_DISCHARGED = 0.9).
- An unverified agent suggestion can appear in §9 (Future Work) but
  NEVER in §7 (Evaluation) or §4 (Framework).

The paper generation pipeline:
1. Partition all sections by trust level
2. Build an "evidence manifest" of what can go where
3. Generate the paper with the manifest as hard constraints
4. Post-generation: scan the paper for any claim not in the manifest
   and flag it as an obstruction (HALLUCINATION on E∩P overlap)

This is the REPORT morphism (E→P) and FORMALIZE morphism (T→P) of the
workspace site, with trust algebra enforcement.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import shutil
import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    TRUST_RUNTIME,
    TRUST_SOLVER,
    TRUST_VERIFIED,
    LLMSection,
)
from jugeo.directed_research._agent_channel import agent_call


# ═══════════════════════════════════════════════════════════════════════
#  Evidence manifest — what claims are allowed where
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class EvidenceManifest:
    """Partitions available evidence by trust level.

    This is the pre-computed constraint set that the paper generator must
    respect. Every number, every claim, every result in the paper must
    come from this manifest, and it can only appear in sections appropriate
    to its trust level.
    """
    # Trust >= RUNTIME (0.7): can appear in Evaluation/Results
    runtime_witnessed: dict[str, Any] = field(default_factory=dict)
    # Trust >= SOLVER (0.9): can appear in Theorems/Proofs
    solver_discharged: dict[str, Any] = field(default_factory=dict)
    # Trust == COPILOT (0.3): can appear in Discussion/Future Work ONLY
    copilot_suggested: dict[str, Any] = field(default_factory=dict)
    # Code metrics (always runtime-witnessed)
    code_metrics: dict[str, Any] = field(default_factory=dict)
    # What we DON'T have evidence for (must NOT appear as claims)
    no_evidence: list[str] = field(default_factory=list)


def build_evidence_manifest(
    benchmark_results: dict[str, Any],
    sections: list[LLMSection],
    code_files: list[str],
) -> EvidenceManifest:
    """Build the evidence manifest from all available sections.

    Partitions every piece of evidence by trust level so the paper
    generator knows what it's allowed to claim and where.
    """
    manifest = EvidenceManifest()

    # Benchmark results are runtime-witnessed
    for key, value in benchmark_results.items():
        if isinstance(value, (int, float)):
            manifest.runtime_witnessed[key] = value
        elif isinstance(value, bool):
            manifest.runtime_witnessed[key] = value
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (int, float, bool)):
                    manifest.runtime_witnessed[f"{key}.{k}"] = v

    # Code metrics
    py_files = [f for f in code_files if f.endswith(".py") and os.path.exists(f)]
    total_lines = 0
    for f in py_files:
        try:
            total_lines += len(open(f).readlines())
        except Exception:
            pass
    manifest.code_metrics["total_py_files"] = len(py_files)
    manifest.code_metrics["total_lines"] = total_lines
    manifest.runtime_witnessed.update(manifest.code_metrics)

    # Partition sections by trust
    for s in sections:
        if s.trust >= TRUST_SOLVER:
            manifest.solver_discharged[s.coordinate] = {
                "surface": s.surface.value, "trust": s.trust}
        elif s.trust >= TRUST_RUNTIME:
            manifest.runtime_witnessed[s.coordinate] = {
                "surface": s.surface.value, "trust": s.trust}
        else:
            manifest.copilot_suggested[s.coordinate] = {
                "surface": s.surface.value, "trust": s.trust}

    return manifest


# ═══════════════════════════════════════════════════════════════════════
#  Trust-gated paper generation
# ═══════════════════════════════════════════════════════════════════════

def generate_paper(
    *,
    approach: str,
    prompt: str,
    theory_text: str,
    module_code: dict[str, str],
    domain_analysis: dict,
    benchmark_results: dict[str, Any],
    ideation_metadata: dict[str, Any],
    output_dir: str,
    sections: Optional[list[LLMSection]] = None,
    code_files: Optional[list[str]] = None,
) -> tuple[str, LLMSection]:
    """Generate a 12-page conference paper with trust-gated claims.

    The paper is a FORMALIZE morphism (T→P) and REPORT morphism (E→P)
    on the workspace site. Trust levels gate what can appear where:

    - §7 Evaluation: ONLY runtime-witnessed numbers (actually computed)
    - §4 Framework: theorems at solver-discharged trust (jg prove passed)
    - §8 Discussion: copilot-suggested ideas are allowed here
    - §9 Future Work: copilot-suggested conjectures go here

    The evidence manifest is passed to the agent as a hard constraint.
    Post-generation, we scan for any number not in the manifest.
    """
    # Build the evidence manifest
    manifest = build_evidence_manifest(
        benchmark_results, sections or [], code_files or [])

    modules_list = ", ".join(module_code.keys())
    total_lines = sum(len(c.splitlines()) for c in module_code.values())

    # Format the manifest as constraints for the agent
    manifest_text = _format_manifest_for_prompt(manifest, domain_analysis)

    path = os.path.join(output_dir, "conference_tool_track.tex")

    section = agent_call(
        textwrap.dedent(f"""\
            Write a COMPREHENSIVE conference paper in LaTeX to the file {path}.
            At least 12 pages when compiled. Tool-track paper for a top venue.

            TOOL: {approach}
            PRODUCT: {prompt}
            MATHEMATICAL APPROACH (first 1200 chars): {theory_text[:1200]}
            MODULES: {modules_list} ({total_lines} total lines)
            IDEATION ORIGIN: Cross-domain synthesis between
            {ideation_metadata.get('domain_name', 'N/A')} and
            {ideation_metadata.get('partner', 'N/A')}.

            ═══════════════════════════════════════════════════════════════
            EVIDENCE MANIFEST — HARD CONSTRAINTS ON WHAT YOU MAY CLAIM
            ═══════════════════════════════════════════════════════════════
            {manifest_text}

            TRUST-GATING RULES (THESE ARE ABSOLUTE):
            1. §7 EVALUATION: You may ONLY cite numbers from RUNTIME_WITNESSED
               above. If a metric is not listed, do NOT report a value for it.
               Instead, note it as "not yet evaluated" in the Discussion.
            2. §4 FRAMEWORK: Theorems may be stated, but proof sketches must
               note which are SOLVER_DISCHARGED (formally verified) vs
               COPILOT_SUGGESTED (conjectured). Use \\textbf{{Verified}} or
               \\textbf{{Conjectured}} tags.
            3. §8 DISCUSSION: This is where you discuss limitations, threats
               to validity, and results you WISH you had but don't.
            4. §9 FUTURE WORK: COPILOT_SUGGESTED ideas go here, clearly
               marked as unverified directions.
            5. NEVER fabricate a number. NEVER invent an experiment you
               didn't run. NEVER cite a paper that doesn't exist. If you're
               unsure whether a result is real, put it in Future Work.

            Structure (EVERY section REQUIRED and SUBSTANTIAL):
            1. ABSTRACT (200+ words) — problem, approach, key VERIFIED results
            2. INTRODUCTION (2+ pages) — motivation, gap, contribution, outline
            3. BACKGROUND & RELATED WORK (1.5+ pages) — existing tools with
               real citations, mathematical prerequisites
            4. MATHEMATICAL FRAMEWORK (2+ pages) — formal definitions, theorems
               tagged as Verified/Conjectured, algorithms with pseudocode
            5. SYSTEM ARCHITECTURE (1.5+ pages) — module diagram, data flow,
               design rationale, how the math maps to code
            6. IMPLEMENTATION (1+ pages) — key data structures, library
               integration, code examples (must be real extracts)
            7. EVALUATION (2+ pages) — experimental setup, metrics from the
               manifest ONLY, baseline comparison, analysis
            8. DISCUSSION (0.5+ pages) — limitations, what we couldn't
               evaluate yet, threats to validity
            9. CONCLUSION & FUTURE WORK (0.5+ pages) — summary + unverified
               directions clearly marked as conjectural
            10. REFERENCES (15+ entries — use REAL papers only, cite by
                first-author-year format)

            Use \\documentclass[11pt]{{article}} with amsmath, booktabs,
            graphicx, hyperref, algorithm2e. Include at least 2 tables
            and 1 algorithm block.

            Write the complete .tex file to {path} using your file-write tool.
            Do NOT return the LaTeX as text — write it to the file.
        """),
        surface=SurfaceKind.CLAIMS,
        coordinate="claims.paper",
        working_dir=output_dir,
    )

    # Read back what the agent wrote
    if os.path.exists(path) and os.path.getsize(path) > 200:
        with open(path) as f:
            paper = f.read()
    else:
        # Fallback: agent returned text instead of writing
        paper = section.content
        if paper.startswith("```"):
            paper = "\n".join(l for l in paper.split("\n") if not l.startswith("```"))

    # Post-generation: scan for fabricated numbers
    fabrication_warnings = _scan_for_fabricated_claims(paper, manifest)
    if fabrication_warnings:
        # Append warnings as LaTeX comments at the end
        warning_block = "\n".join(
            f"% WARNING: Possible fabricated claim — {w}"
            for w in fabrication_warnings
        )
        paper = paper.rstrip() + "\n\n" + warning_block + "\n"

    path = os.path.join(output_dir, "conference_tool_track.tex")
    with open(path, "w") as f:
        f.write(paper.strip() + "\n")

    # Try compiling
    if shutil.which("pdflatex"):
        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode",
                 "conference_tool_track.tex"],
                capture_output=True, timeout=30, cwd=output_dir)
            # Run twice for references
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode",
                 "conference_tool_track.tex"],
                capture_output=True, timeout=30, cwd=output_dir)
        except Exception:
            pass

    return path, section


# ═══════════════════════════════════════════════════════════════════════
#  Manifest formatting and fabrication scanning
# ═══════════════════════════════════════════════════════════════════════

def _format_manifest_for_prompt(
    manifest: EvidenceManifest,
    domain_analysis: dict,
) -> str:
    """Format the evidence manifest as text for the agent prompt."""
    lines = []

    lines.append("RUNTIME_WITNESSED (trust >= 0.7 — may appear in Evaluation):")
    for k, v in manifest.runtime_witnessed.items():
        if isinstance(v, (int, float, bool)):
            lines.append(f"  {k} = {v}")
    if not manifest.runtime_witnessed:
        lines.append("  (no runtime-witnessed evidence yet)")

    lines.append("")
    lines.append("SOLVER_DISCHARGED (trust >= 0.9 — may appear as Theorem):")
    for k, v in manifest.solver_discharged.items():
        lines.append(f"  {k}: verified")
    if not manifest.solver_discharged:
        lines.append("  (no solver-verified claims yet)")

    lines.append("")
    lines.append("COPILOT_SUGGESTED (trust = 0.3 — Discussion/Future Work ONLY):")
    for k, v in manifest.copilot_suggested.items():
        if isinstance(v, dict):
            lines.append(f"  {k} ({v.get('surface', '?')})")
    if not manifest.copilot_suggested:
        lines.append("  (none)")

    lines.append("")
    lines.append("BASELINES (from domain analysis — include in comparison):")
    for b in domain_analysis.get("baselines_to_beat", []):
        lines.append(f"  {b.get('name', '?')}: {b.get('metric', '?')} = {b.get('value', '?')}")

    return "\n".join(lines)


def _scan_for_fabricated_claims(
    paper_text: str,
    manifest: EvidenceManifest,
) -> list[str]:
    """Scan the paper for numbers that don't appear in the evidence manifest.

    This is the post-generation descent check on the E∩P overlap: any
    numerical claim in the Evaluation section that doesn't match a
    runtime-witnessed value is flagged as a potential fabrication.
    """
    warnings: list[str] = []

    # Extract the Evaluation section (between \section{Evaluation} and next \section)
    eval_match = re.search(
        r'\\section\{[^}]*[Ee]valuation[^}]*\}(.*?)\\section\{',
        paper_text, re.DOTALL)
    if not eval_match:
        return warnings

    eval_text = eval_match.group(1)

    # Find all decimal numbers in the evaluation section
    numbers_in_eval = re.findall(r'(?<!\{)\b(\d+\.\d+)\b(?!\})', eval_text)

    # Check each against the manifest
    manifest_values = set()
    for v in manifest.runtime_witnessed.values():
        if isinstance(v, (int, float)):
            manifest_values.add(f"{v:.4f}"[:6])  # match to 4 decimal places
            manifest_values.add(f"{v:.2f}")
            manifest_values.add(f"{v:.1f}")
            manifest_values.add(str(int(v)) if v == int(v) else "")

    for num_str in numbers_in_eval:
        try:
            num = float(num_str)
            # Check if this number matches any manifest value (within tolerance)
            matched = any(
                abs(num - v) < 0.02
                for v in manifest.runtime_witnessed.values()
                if isinstance(v, (int, float))
            )
            if not matched and num > 0.01:  # skip very small numbers (likely formatting)
                warnings.append(
                    f"Number {num_str} in Evaluation section not found in "
                    f"evidence manifest (possible fabrication)")
        except ValueError:
            pass

    return warnings
