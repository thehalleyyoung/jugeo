"""LLM judge for synthesis frontier — scores field pairings for integration potential.
# copilot: synthesis frontier llm judge — evaluates cross-domain integration scores
"""
from __future__ import annotations

import json
import re
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from statistics import mean
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model imports with stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.synthesis_frontier.models import (
        FieldNode,
        SynthesisPair,
        MetaphorLink,
        PropositionRecord,
    )
except ImportError:  # pragma: no cover
    # Stubs so the module can be imported without the full jugeo package.

    class FieldNode:  # type: ignore[no-redef]
        """Stub FieldNode."""

        def __init__(
            self,
            field_id: str,
            name: str,
            description: str,
            keywords: tuple[str, ...] = (),
            propositions: list[Any] | None = None,
            constituent_fields: list[str] | None = None,
        ) -> None:
            self.field_id = field_id
            self.name = name
            self.description = description
            self.keywords = keywords
            self.propositions = propositions or []
            self.constituent_fields = constituent_fields or []

        @classmethod
        def make(
            cls,
            name: str,
            description: str,
            keywords: tuple[str, ...] = (),
            propositions: list[Any] | None = None,
            constituent_fields: list[str] | None = None,
        ) -> "FieldNode":
            import uuid
            fid = str(uuid.uuid4())
            return cls(fid, name, description, keywords, propositions, constituent_fields)

    @dataclass
    class MetaphorLink:  # type: ignore[no-redef]
        """Stub MetaphorLink."""
        link_id: str
        source_field_id: str
        target_field_id: str
        source_concept: str
        target_concept: str
        description: str
        strength: float
        kind: str
        supporting_propositions: tuple
        created_at: float
        is_known_classical: bool = False

        @staticmethod
        def make(
            source_field_id: str,
            target_field_id: str,
            source_concept: str,
            target_concept: str,
            description: str,
            strength: float = 0.5,
            kind: str = "structural",
            supporting_propositions: tuple = (),
            is_known_classical: bool = False,
        ) -> "MetaphorLink":
            import uuid, time
            return MetaphorLink(
                link_id=str(uuid.uuid4()),
                source_field_id=source_field_id,
                target_field_id=target_field_id,
                source_concept=source_concept,
                target_concept=target_concept,
                description=description,
                strength=strength,
                kind=kind,
                supporting_propositions=supporting_propositions,
                created_at=time.time(),
                is_known_classical=is_known_classical,
            )

    @dataclass
    class PropositionRecord:  # type: ignore[no-redef]
        """Stub PropositionRecord."""
        prop_id: str
        title: str
        statement: str
        kind: Any
        source_field_id: str
        tags: tuple
        importance: float
        created_at: float
        proof_sketch: str
        references: tuple

        @staticmethod
        def make(
            title: str,
            statement: str,
            kind: Any = "theorem",
            source_field_id: str = "",
            tags: tuple = (),
            importance: float = 0.5,
            proof_sketch: str = "",
            references: tuple = (),
        ) -> "PropositionRecord":
            import uuid, time
            return PropositionRecord(
                prop_id=str(uuid.uuid4()),
                title=title,
                statement=statement,
                kind=kind,
                source_field_id=source_field_id,
                tags=tags,
                importance=importance,
                created_at=time.time(),
                proof_sketch=proof_sketch,
                references=references,
            )

    @dataclass
    class SynthesisPair:  # type: ignore[no-redef]
        """Stub SynthesisPair."""
        pair_id: str
        field_a_id: str
        field_b_id: str
        integration_score: float
        leverage: float
        metaphor_richness: float
        transportability: float
        proof_density: float
        novelty: float
        geometry_fit: float
        metaphors: tuple
        bridge_theorems: tuple
        reasoning: str
        created_at: float

        @staticmethod
        def make(
            field_a_id: str,
            field_b_id: str,
            integration_score: float,
            leverage: float = 0.5,
            metaphor_richness: float = 0.5,
            transportability: float = 0.5,
            proof_density: float = 0.5,
            novelty: float = 0.5,
            geometry_fit: float = 0.5,
            metaphors: tuple = (),
            bridge_theorems: tuple = (),
            reasoning: str = "",
        ) -> "SynthesisPair":
            import uuid, time
            return SynthesisPair(
                pair_id=str(uuid.uuid4()),
                field_a_id=field_a_id,
                field_b_id=field_b_id,
                integration_score=integration_score,
                leverage=leverage,
                metaphor_richness=metaphor_richness,
                transportability=transportability,
                proof_density=proof_density,
                novelty=novelty,
                geometry_fit=geometry_fit,
                metaphors=metaphors,
                bridge_theorems=bridge_theorems,
                reasoning=reasoning,
                created_at=time.time(),
            )


# ---------------------------------------------------------------------------
# Enums & configuration
# ---------------------------------------------------------------------------

class JudgeMode(str, Enum):
    """Operating mode for SynthesisJudge."""

    LLM = "llm"
    HEURISTIC = "heuristic"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class JudgeConfig:
    """Configuration for the synthesis judge."""

    model: str = "claude-sonnet-4-6"
    mode: JudgeMode = JudgeMode.HEURISTIC
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_seconds: float = 30.0
    retry_attempts: int = 2


# ---------------------------------------------------------------------------
# JudgeVerdict
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JudgeVerdict:
    """Scored verdict for a field pairing."""

    pair_id: str
    integration_score: float
    leverage: float
    metaphor_richness: float
    transportability: float
    proof_density: float
    novelty: float
    geometry_fit: float
    reasoning: str
    metaphors_found: list[dict[str, Any]]
    bridge_theorem_sketches: list[dict[str, Any]]
    judged_at: float
    mode_used: JudgeMode

    # ------------------------------------------------------------------
    def to_synthesis_pair(self, field_a: FieldNode, field_b: FieldNode) -> SynthesisPair:
        """Convert this verdict into a SynthesisPair with full metadata."""
        metaphors: list[MetaphorLink] = []
        for m in self.metaphors_found:
            try:
                ml = MetaphorLink.make(
                    source_field_id=field_a.field_id,
                    target_field_id=field_b.field_id,
                    source_concept=str(m.get("source", field_a.name)),
                    target_concept=str(m.get("target", field_b.name)),
                    description=str(m.get("description", "")),
                    strength=float(m.get("strength", self.metaphor_richness)),
                    kind=str(m.get("kind", "structural")),
                )
                metaphors.append(ml)
            except Exception as exc:
                logger.debug("Skipping metaphor due to error: %s", exc)

        bridge_theorems: list[PropositionRecord] = []
        merged_id = f"{field_a.field_id[:8]}_{field_b.field_id[:8]}"
        for bt in self.bridge_theorem_sketches:
            try:
                pr = PropositionRecord.make(
                    title=str(bt.get("title", "Bridge Theorem")),
                    statement=str(bt.get("statement", "")),
                    kind="bridge_theorem",
                    source_field_id=merged_id,
                    tags=("bridge",),
                    importance=0.75,
                    proof_sketch=str(bt.get("sketch", "")),
                )
                bridge_theorems.append(pr)
            except Exception as exc:
                logger.debug("Skipping bridge theorem due to error: %s", exc)

        return SynthesisPair.make(
            field_a_id=field_a.field_id,
            field_b_id=field_b.field_id,
            integration_score=self.integration_score,
            leverage=self.leverage,
            metaphor_richness=self.metaphor_richness,
            transportability=self.transportability,
            proof_density=self.proof_density,
            novelty=self.novelty,
            geometry_fit=self.geometry_fit,
            metaphors=tuple(metaphors),
            bridge_theorems=tuple(bridge_theorems),
            reasoning=self.reasoning,
        )


# ---------------------------------------------------------------------------
# Domain cluster helper
# ---------------------------------------------------------------------------

_DOMAIN_CLUSTERS: dict[str, set[str]] = {
    "logic": {"logic", "proof", "proposition", "deduction", "inference", "formal", "axiom", "theorem"},
    "algebra": {"algebra", "group", "ring", "field", "module", "morphism", "functor", "category", "adjoint"},
    "geometry": {"geometry", "topology", "manifold", "sheaf", "fiber", "bundle", "site", "cohomology", "homology"},
    "analysis": {"analysis", "calculus", "measure", "integral", "differential", "limit", "continuity"},
    "combinatorics": {"combinatorics", "graph", "tree", "permutation", "partition", "lattice", "poset"},
    "quantum": {"quantum", "hilbert", "operator", "spectrum", "state", "entanglement", "unitary"},
    "computation": {"type", "term", "lambda", "computation", "algorithm", "complexity", "program", "language"},
    "probability": {"probability", "stochastic", "random", "distribution", "expectation", "bayes"},
}

_GEOMETRY_KEYWORDS: frozenset[str] = frozenset(
    {"geometry", "topology", "sheaf", "fiber", "site", "judgment", "space", "topos", "locale", "frame"}
)


def _cluster_for(keywords: tuple[str, ...]) -> str | None:
    """Return the best-matching domain cluster name for a keyword set."""
    kw_set = {k.lower() for k in keywords}
    best_cluster: str | None = None
    best_count = 0
    for cluster_name, cluster_kws in _DOMAIN_CLUSTERS.items():
        count = len(kw_set & cluster_kws)
        if count > best_count:
            best_count = count
            best_cluster = cluster_name
    return best_cluster


def _domain_distance(field_a: FieldNode, field_b: FieldNode) -> float:
    """Estimate [0, 1] distance between two fields based on domain clusters."""
    ca = _cluster_for(field_a.keywords)
    cb = _cluster_for(field_b.keywords)
    if ca is None or cb is None:
        return 0.5
    if ca == cb:
        return 0.05
    _CLOSE_PAIRS: set[frozenset[str]] = {
        frozenset({"logic", "computation"}),
        frozenset({"logic", "algebra"}),
        frozenset({"algebra", "geometry"}),
        frozenset({"geometry", "analysis"}),
        frozenset({"analysis", "probability"}),
        frozenset({"algebra", "combinatorics"}),
        frozenset({"computation", "combinatorics"}),
    }
    _MEDIUM_PAIRS: set[frozenset[str]] = {
        frozenset({"logic", "geometry"}),
        frozenset({"algebra", "analysis"}),
        frozenset({"algebra", "quantum"}),
        frozenset({"geometry", "quantum"}),
        frozenset({"computation", "logic"}),
    }
    pair_key = frozenset({ca, cb})
    if pair_key in _CLOSE_PAIRS:
        return 0.25
    if pair_key in _MEDIUM_PAIRS:
        return 0.55
    return 0.80


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity of two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _geometry_fit(field: FieldNode) -> float:
    """Score how geometry/topology-flavoured a field is."""
    kw_lower = {k.lower() for k in field.keywords}
    desc_lower = field.description.lower()
    hits = sum(1 for gk in _GEOMETRY_KEYWORDS if gk in kw_lower or gk in desc_lower)
    return min(1.0, hits / 3.0)


# ---------------------------------------------------------------------------
# HeuristicJudge
# ---------------------------------------------------------------------------

class HeuristicJudge:
    """Scores field pairings using purely structural / keyword heuristics."""

    def score_pair(self, field_a: FieldNode, field_b: FieldNode) -> JudgeVerdict:
        """Return a JudgeVerdict using heuristic scoring only."""
        import uuid

        kw_a: set[str] = {k.lower() for k in field_a.keywords}
        kw_b: set[str] = {k.lower() for k in field_b.keywords}

        keyword_overlap: float = _jaccard(kw_a, kw_b)
        domain_dist: float = _domain_distance(field_a, field_b)

        prop_count_a = len(field_a.propositions) if field_a.propositions else 0
        prop_count_b = len(field_b.propositions) if field_b.propositions else 0
        prop_richness: float = (prop_count_a + prop_count_b) / 2.0

        const_a = len(field_a.constituent_fields) if field_a.constituent_fields else 0
        const_b = len(field_b.constituent_fields) if field_b.constituent_fields else 0
        constituent_diff = abs(const_a - const_b)
        constituent_novelty: float = min(1.0, constituent_diff / 5.0)

        leverage: float = 0.3 + 0.4 * domain_dist + 0.3 * keyword_overlap
        metaphor_richness: float = keyword_overlap * 0.6 + 0.2 * min(1.0, prop_richness / 5.0)
        transportability: float = 0.4 + 0.3 * keyword_overlap + 0.3 * (1.0 - domain_dist)
        proof_density: float = min(1.0, prop_richness / 8.0)
        novelty: float = domain_dist * 0.7 + constituent_novelty * 0.3
        geo_a = _geometry_fit(field_a)
        geo_b = _geometry_fit(field_b)
        geometry_fit: float = (geo_a + geo_b) / 2.0
        integration_score: float = mean([
            leverage,
            metaphor_richness,
            transportability,
            proof_density,
            novelty,
            geometry_fit,
        ])

        def _clamp(v: float) -> float:
            return max(0.0, min(1.0, v))

        leverage = _clamp(leverage)
        metaphor_richness = _clamp(metaphor_richness)
        transportability = _clamp(transportability)
        proof_density = _clamp(proof_density)
        novelty = _clamp(novelty)
        geometry_fit = _clamp(geometry_fit)
        integration_score = _clamp(integration_score)

        shared_kw = sorted(kw_a & kw_b)
        unique_a = sorted(kw_a - kw_b)[:3]
        unique_b = sorted(kw_b - kw_a)[:3]

        metaphors_found: list[dict[str, Any]] = []

        if shared_kw:
            metaphors_found.append({
                "source": field_a.name,
                "target": field_b.name,
                "description": (
                    f"Both fields share the notion of '{shared_kw[0]}', "
                    f"suggesting a common structural pattern."
                ),
                "strength": round(keyword_overlap * 0.9, 3),
            })

        if unique_a and unique_b:
            metaphors_found.append({
                "source": field_a.name,
                "target": field_b.name,
                "description": (
                    f"The concept of '{unique_a[0]}' in {field_a.name} "
                    f"may transport as '{unique_b[0]}' in {field_b.name}, "
                    f"offering a cross-domain metaphor."
                ),
                "strength": round(0.3 + domain_dist * 0.4, 3),
            })

        if domain_dist > 0.5:
            metaphors_found.append({
                "source": field_b.name,
                "target": field_a.name,
                "description": (
                    f"The structural gap between {field_a.name} and {field_b.name} "
                    f"creates fertile ground for analogical bridges \u2014 "
                    f"especially around their respective notions of composition."
                ),
                "strength": round(domain_dist * 0.6, 3),
            })

        bridge_theorem_sketches: list[dict[str, Any]] = [
            {
                "title": f"Transport Theorem: {field_a.name} \u2192 {field_b.name}",
                "statement": (
                    f"There exists a functor-like mapping F: {field_a.name} \u2192 {field_b.name} "
                    f"preserving the core structural relations identified by the shared vocabulary "
                    f"{{{', '.join(shared_kw[:3]) if shared_kw else 'core notions'}}}."
                ),
                "sketch": (
                    "Define the mapping on generators, verify preservation of composition "
                    "and identity, then extend by universality or adjunction."
                ),
            },
            {
                "title": f"Integration Lemma: {field_b.name} \u2194 {field_a.name}",
                "statement": (
                    f"Any theorem in {field_a.name} whose proof uses only "
                    f"{{{', '.join(unique_a[:2]) if unique_a else 'basic axioms'}}} "
                    f"admits an analogous statement in {field_b.name} via the bridge functor."
                ),
                "sketch": (
                    "Apply the transport theorem; check that the image lies in the "
                    "appropriate subcategory; verify coherence conditions."
                ),
            },
        ]

        reasoning = (
            f"Heuristic evaluation of ({field_a.name}, {field_b.name}): "
            f"keyword overlap={keyword_overlap:.3f}, domain distance={domain_dist:.3f}, "
            f"prop richness={prop_richness:.1f}. "
            f"Integration score={integration_score:.3f} reflects "
            f"{'high cross-domain potential' if integration_score > 0.6 else 'moderate integration potential'}."
        )

        return JudgeVerdict(
            pair_id=str(uuid.uuid4()),
            integration_score=round(integration_score, 4),
            leverage=round(leverage, 4),
            metaphor_richness=round(metaphor_richness, 4),
            transportability=round(transportability, 4),
            proof_density=round(proof_density, 4),
            novelty=round(novelty, 4),
            geometry_fit=round(geometry_fit, 4),
            reasoning=reasoning,
            metaphors_found=metaphors_found,
            bridge_theorem_sketches=bridge_theorem_sketches,
            judged_at=time.time(),
            mode_used=JudgeMode.HEURISTIC,
        )


# ---------------------------------------------------------------------------
# LLMJudge
# ---------------------------------------------------------------------------
_PROMPT_TEMPLATE = """[CONTEXT]
You are an expert mathematical judge operating within the JuGeo worldview — a framework
that seeks the deepest possible unifications across mathematical and conceptual fields.
Your task is to evaluate how strongly two mathematical/conceptual fields can be integrated,
and to surface the most productive bridges, rewrites, and transferable techniques.

The bridges you identify should be BROADLY USEFUL across mathematics — not limited to
formal verification or logic. Prioritize connections that yield:
- Computational techniques (numerical algorithms, approximation schemes, efficient representations)
- Geometric insights (manifold constructions, metric structures, curvature interpretations)
- Algebraic machinery (functorial constructions, spectral sequences, representation-theoretic tools)
- Analytical methods (inequalities, asymptotic analysis, PDE techniques)
- Combinatorial structures (generating functions, counting arguments, graph-theoretic reformulations)

[FIELD_A]
Name: {name_a}
Description: {desc_a}
Keywords: {kw_a}
Proposition count: {prop_count_a}
Constituent fields: {const_a}

[FIELD_B]
Name: {name_b}
Description: {desc_b}
Keywords: {kw_b}
Proposition count: {prop_count_b}
Constituent fields: {const_b}

[TASK]
Score the integration potential of these two fields. Consider:
1. Shared structural motifs and transferable techniques
2. Potential for functor-like mappings or adjunctions between their categories
3. Metaphor richness: how many productive analogies exist
4. Transportability: how easily theorems from one field apply to the other
5. Proof density: how much formal content can be transported
6. Novelty: how surprising / non-obvious the connection is
7. Breadth of applicability: does the bridge unlock tools for numerical computing,
   geometry, algebra, analysis, combinatorics, physics — not just one niche?

For bridge_theorem_sketches, provide REAL mathematical content that is broadly applicable:
- Each theorem should state a precise mathematical claim connecting the two fields
- Include a proof sketch showing the key insight (not just "follows from definitions")
- Reference specific mathematical structures (groups, spaces, functors, sheaves, etc.)
- At least one bridge should yield a COMPUTATIONAL method (an algorithm, a rewrite rule,
  a numerical scheme, or a constructive procedure someone could implement)
- At least one bridge should connect to GEOMETRY or ANALYSIS (manifolds, metrics,
  curvature, differential operators, measure theory, functional analysis)

For metaphors_found, describe concrete structural analogies:
- Name specific objects/operations in each field that correspond
- Explain what the correspondence reveals and where else it could be applied

[OUTPUT_FORMAT]
Respond ONLY with a JSON block wrapped in ```json ... ```.
The JSON must match this schema exactly:
{{
  "integration_score": <float 0-1>,
  "leverage": <float 0-1>,
  "metaphor_richness": <float 0-1>,
  "transportability": <float 0-1>,
  "proof_density": <float 0-1>,
  "novelty": <float 0-1>,
  "geometry_fit": <float 0-1>,
  "reasoning": "<one paragraph explaining the deepest connection and its broad applicability>",
  "metaphors_found": [
    {{"source": "<field name>", "target": "<field name>", "description": "<specific structural analogy>", "strength": <float 0-1>}},
    ...
  ],
  "bridge_theorem_sketches": [
    {{"title": "<precise title>", "statement": "<formal mathematical statement>", "sketch": "<proof sketch with key insight>"}},
    ...
  ]
}}
"""


class LLMJudge:
    """Judges field pairings by calling an LLM (Anthropic Claude or OpenAI)."""

    def __init__(self, config: JudgeConfig) -> None:
        self.config = config
        self._heuristic_fallback = HeuristicJudge()

    def score_pair(self, field_a: FieldNode, field_b: FieldNode) -> JudgeVerdict:
        """Score a pair via LLM, falling back to heuristic on failure."""
        prompt = self._build_prompt(field_a, field_b)
        last_exc: Exception | None = None

        for attempt in range(max(1, self.config.retry_attempts)):
            try:
                response_text = self._call_llm(prompt)
                verdict = self._parse_response(response_text, pair_id=None)
                import dataclasses as _dc
                verdict = _dc.replace(verdict, mode_used=JudgeMode.LLM)
                return verdict
            except Exception as exc:
                last_exc = exc
                logger.warning("LLM attempt %d failed: %s", attempt + 1, exc)

        logger.warning(
            "LLM judge failed after %d attempts; using heuristic fallback. Error: %s",
            self.config.retry_attempts,
            last_exc,
        )
        return self._heuristic_fallback.score_pair(field_a, field_b)

    def _build_prompt(self, field_a: FieldNode, field_b: FieldNode) -> str:
        """Construct the multi-section prompt for the LLM."""
        prop_count_a = len(field_a.propositions) if field_a.propositions else 0
        prop_count_b = len(field_b.propositions) if field_b.propositions else 0
        return _PROMPT_TEMPLATE.format(
            name_a=field_a.name,
            desc_a=field_a.description,
            kw_a=", ".join(field_a.keywords),
            prop_count_a=prop_count_a,
            const_a=", ".join(field_a.constituent_fields) if field_a.constituent_fields else "none",
            name_b=field_b.name,
            desc_b=field_b.description,
            kw_b=", ".join(field_b.keywords),
            prop_count_b=prop_count_b,
            const_b=", ".join(field_b.constituent_fields) if field_b.constituent_fields else "none",
        )

    def _call_copilot(self, prompt: str) -> str:
        """Call Copilot CLI with gpt-5.4 model."""
        import subprocess
        import shutil
        import tempfile

        if not shutil.which("copilot"):
            raise RuntimeError("copilot CLI not found on PATH")

        # Use isolated empty dir to prevent copilot from reading local files
        tmpdir = tempfile.mkdtemp(prefix="jugeo_llm_")
        try:
            result = subprocess.run(
                ["copilot", "-p", prompt, "--model", "gpt-5.4",
                 "--available-tools", ""],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=tmpdir,
            )
        finally:
            import os
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
        if result.returncode != 0:
            raise RuntimeError(
                f"copilot CLI failed (rc={result.returncode}): {result.stderr[:300]}"
            )
        text = result.stdout.strip()
        if not text:
            raise RuntimeError("copilot CLI returned empty response")
        # Strip tool narration lines from copilot output
        lines = text.split("\n")
        cleaned = []
        skip_block = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("●") or stripped.startswith("✗"):
                skip_block = True
                continue
            if skip_block and (stripped.startswith("│") or stripped.startswith("└")):
                continue
            if skip_block and stripped == "":
                continue
            skip_block = False
            cleaned.append(line)
        while cleaned and not cleaned[0].strip():
            cleaned.pop(0)
        return "\n".join(cleaned).strip()

    def _call_llm(self, prompt: str) -> str:
        """Dispatch to Copilot, Anthropic, or OpenAI — in that order."""
        try:
            return self._call_copilot(prompt)
        except Exception as exc:
            logger.debug("Copilot provider failed: %s", exc)

        try:
            import anthropic  # type: ignore[import]
            client = anthropic.Anthropic()
            message = client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except (ImportError, Exception) as exc:
            logger.debug("Anthropic provider failed: %s", exc)

        try:
            import openai  # type: ignore[import]
            client = openai.OpenAI()
            response = client.chat.completions.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=[{"role": "user", "content": prompt}],
                timeout=self.config.timeout_seconds,
            )
            return response.choices[0].message.content or ""
        except (ImportError, Exception) as exc:
            logger.debug("OpenAI provider failed: %s", exc)

        raise RuntimeError(
            "No LLM provider available. Copilot requires `gh` CLI, "
            "or install anthropic/openai packages with valid API keys."
        )

    def _parse_response(self, response_text: str, pair_id: str | None) -> JudgeVerdict:
        """Parse LLM response into a JudgeVerdict; falls back gracefully."""
        import uuid

        pid = pair_id or str(uuid.uuid4())

        json_match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                return self._dict_to_verdict(data, pid)
            except json.JSONDecodeError as exc:
                logger.debug("JSON block parse failed: %s", exc)

        try:
            data = json.loads(response_text.strip())
            return self._dict_to_verdict(data, pid)
        except json.JSONDecodeError:
            pass

        scores: dict[str, float] = {}
        for key in ("integration_score", "leverage", "metaphor_richness",
                    "transportability", "proof_density", "novelty", "geometry_fit"):
            m = re.search(rf"{key}[:\s]+([0-9.]+)", response_text, re.IGNORECASE)
            if m:
                try:
                    scores[key] = float(m.group(1))
                except ValueError:
                    pass

        reasoning_match = re.search(r"reasoning[:\s]+(.+?)(?:\n|$)", response_text, re.IGNORECASE)
        reasoning = (
            reasoning_match.group(1).strip()
            if reasoning_match
            else "Parsed from unstructured LLM response."
        )

        return JudgeVerdict(
            pair_id=pid,
            integration_score=scores.get("integration_score", 0.5),
            leverage=scores.get("leverage", 0.5),
            metaphor_richness=scores.get("metaphor_richness", 0.4),
            transportability=scores.get("transportability", 0.5),
            proof_density=scores.get("proof_density", 0.3),
            novelty=scores.get("novelty", 0.5),
            geometry_fit=scores.get("geometry_fit", 0.3),
            reasoning=reasoning,
            metaphors_found=[],
            bridge_theorem_sketches=[],
            judged_at=time.time(),
            mode_used=JudgeMode.LLM,
        )

    def _dict_to_verdict(self, data: dict[str, Any], pair_id: str) -> JudgeVerdict:
        """Convert a parsed JSON dict into a JudgeVerdict."""
        def _f(key: str, default: float = 0.5) -> float:
            try:
                return float(data.get(key, default))
            except (TypeError, ValueError):
                return default

        return JudgeVerdict(
            pair_id=pair_id,
            integration_score=_f("integration_score"),
            leverage=_f("leverage"),
            metaphor_richness=_f("metaphor_richness"),
            transportability=_f("transportability"),
            proof_density=_f("proof_density"),
            novelty=_f("novelty"),
            geometry_fit=_f("geometry_fit"),
            reasoning=str(data.get("reasoning", "")),
            metaphors_found=list(data.get("metaphors_found", [])),
            bridge_theorem_sketches=list(data.get("bridge_theorem_sketches", [])),
            judged_at=time.time(),
            mode_used=JudgeMode.LLM,
        )


# ---------------------------------------------------------------------------
# SynthesisJudge (main entry point)
# ---------------------------------------------------------------------------

def _average_verdicts(v_llm: JudgeVerdict, v_heuristic: JudgeVerdict) -> JudgeVerdict:
    """Average numeric scores from two verdicts, merging qualitative content."""
    def _avg(a: float, b: float) -> float:
        return round((a + b) / 2.0, 4)

    combined_metaphors = v_llm.metaphors_found + [
        m for m in v_heuristic.metaphors_found if m not in v_llm.metaphors_found
    ]
    combined_sketches = v_llm.bridge_theorem_sketches + [
        s for s in v_heuristic.bridge_theorem_sketches if s not in v_llm.bridge_theorem_sketches
    ]

    return JudgeVerdict(
        pair_id=v_llm.pair_id,
        integration_score=_avg(v_llm.integration_score, v_heuristic.integration_score),
        leverage=_avg(v_llm.leverage, v_heuristic.leverage),
        metaphor_richness=_avg(v_llm.metaphor_richness, v_heuristic.metaphor_richness),
        transportability=_avg(v_llm.transportability, v_heuristic.transportability),
        proof_density=_avg(v_llm.proof_density, v_heuristic.proof_density),
        novelty=_avg(v_llm.novelty, v_heuristic.novelty),
        geometry_fit=_avg(v_llm.geometry_fit, v_heuristic.geometry_fit),
        reasoning=f"[HYBRID] LLM: {v_llm.reasoning} | Heuristic: {v_heuristic.reasoning}",
        metaphors_found=combined_metaphors,
        bridge_theorem_sketches=combined_sketches,
        judged_at=time.time(),
        mode_used=JudgeMode.HYBRID,
    )


class SynthesisJudge:
    """Top-level judge that dispatches to heuristic, LLM, or hybrid scoring."""

    def __init__(self, config: JudgeConfig | None = None) -> None:
        self.config = config or JudgeConfig()
        self._heuristic = HeuristicJudge()
        self._llm: LLMJudge | None = None
        if self.config.mode in (JudgeMode.LLM, JudgeMode.HYBRID):
            self._llm = LLMJudge(self.config)

    def score_pair(self, field_a: FieldNode, field_b: FieldNode) -> JudgeVerdict:
        """Score a single (field_a, field_b) pairing."""
        if self.config.mode == JudgeMode.HEURISTIC:
            return self._heuristic.score_pair(field_a, field_b)

        if self.config.mode == JudgeMode.LLM:
            assert self._llm is not None
            try:
                return self._llm.score_pair(field_a, field_b)
            except Exception as exc:
                logger.warning("LLMJudge failed, falling back to heuristic: %s", exc)
                return self._heuristic.score_pair(field_a, field_b)

        if self.config.mode == JudgeMode.HYBRID:
            assert self._llm is not None
            h_verdict = self._heuristic.score_pair(field_a, field_b)
            try:
                llm_verdict = self._llm.score_pair(field_a, field_b)
                return _average_verdicts(llm_verdict, h_verdict)
            except Exception as exc:
                logger.warning("LLM portion of hybrid failed; using heuristic only: %s", exc)
                return h_verdict

        return self._heuristic.score_pair(field_a, field_b)

    def score_batch(
        self, pairs: list[tuple[FieldNode, FieldNode]]
    ) -> list[JudgeVerdict]:
        """Score a batch of field pairs in sequence."""
        results: list[JudgeVerdict] = []
        for i, (fa, fb) in enumerate(pairs):
            try:
                verdict = self.score_pair(fa, fb)
                results.append(verdict)
            except Exception as exc:
                logger.error("Failed to score pair %d (%s, %s): %s", i, fa.name, fb.name, exc)
        return results


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        from jugeo.ideation.synthesis_frontier.models import FieldNode
        field_a = FieldNode.make("Category Theory", "CT desc", keywords=("functor", "adjoint", "category", "morphism"))
        field_b = FieldNode.make("Type Theory", "TT desc", keywords=("type", "term", "proof", "lambda", "proposition"))
        judge = SynthesisJudge()
        verdict = judge.score_pair(field_a, field_b)
        print(f"Integration score: {verdict.integration_score:.3f}")
        print(f"Leverage: {verdict.leverage:.3f}")
        print(f"Metaphors found: {len(verdict.metaphors_found)}")
        pair = verdict.to_synthesis_pair(field_a, field_b)
        print(f"SynthesisPair created: {pair.pair_id[:8]}...")
    except Exception as e:
        print(f"Smoke test error: {e}")
        import traceback; traceback.print_exc()
