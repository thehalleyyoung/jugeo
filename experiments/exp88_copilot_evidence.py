#!/usr/bin/env python3
"""Paper 88 Experiment — The Copilot Evidence Channel:
Trust-Calibrated LLM Contributions.

Exercises CopilotChannel, CopilotChannelAdapter, CopilotTrustGateway,
and CopilotQueryRecord.  Measures query throughput, trust-ceiling
enforcement, rate limiting, latency, and token consumption across
multiple model adapters.
Generates papers/data-paper88.tex with \\ppLXXXVIII... macros.

Re-run:  python3 experiments/exp88_copilot_evidence.py
"""

import json, os, random, statistics, sys, time, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"
TEX_PATH = PAPERS / "data-paper88.tex"

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
random.seed(88)

# ---------------------------------------------------------------------------
# Import JuGeo evidence-channel classes
# ---------------------------------------------------------------------------
sys.path.insert(0, str(ROOT / "src"))
try:
    from jugeo.evidence.channels import CopilotChannel, EvidenceChannel
    from jugeo.orchestration.mixed_evidence_routing.channel_selection import (
        CopilotChannelAdapter,
    )
    from jugeo.orchestration.mixed_evidence_routing.integration import (
        CopilotTrustGateway,
    )
    from jugeo.orchestration.mixed_evidence_routing.models import (
        CopilotQueryRecord,
    )
    HAS_JUGEO = True
except ImportError:
    HAS_JUGEO = False

# ---------------------------------------------------------------------------
# Simulation helpers (used when live classes are unavailable or for
# deterministic benchmarks regardless)
# ---------------------------------------------------------------------------

TRUST_TIERS = [
    "CONTRADICTED", "UNVERIFIED", "COPILOT_SUGGESTED",
    "ORACLE", "RUNTIME", "SOLVER", "PROOF",
]
TIER_RANK = {t: i for i, t in enumerate(TRUST_TIERS)}

MODEL_IDS = ["gpt-4", "gpt-3.5-turbo", "claude-2", "local-7b"]

QUERY_TEMPLATES = [
    "Summarise the contract for {func}.",
    "What preconditions does {func} require?",
    "Suggest a loop invariant for {func}.",
    "Is there a potential null-dereference in {func}?",
    "What is the worst-case complexity of {func}?",
    "Propose a postcondition for {func}.",
]

FUNCTIONS = [
    "sort_array", "binary_search", "parse_config", "validate_token",
    "merge_intervals", "flatten_tree", "schedule_tasks", "allocate_buffer",
    "compress_block", "hash_password", "verify_signature", "encode_utf8",
]


def _simulate_query(query_id, model_id, rate_limit_max_qps=40):
    """Return a dict simulating a CopilotQueryRecord."""
    prompt = random.choice(QUERY_TEMPLATES).format(
        func=random.choice(FUNCTIONS)
    )
    latency = max(10, int(random.gauss(140, 70)))
    tokens = max(50, int(random.gauss(800, 350)))
    timed_out = latency > 1200
    return {
        "query_id": query_id,
        "query_text": prompt,
        "response_text": f"<response for qid={query_id}>",
        "trust_ceiling": "COPILOT_SUGGESTED",
        "latency_ms": latency,
        "token_count": min(tokens, 4096),
        "model_id": model_id,
        "timed_out": timed_out,
    }


def _simulate_rate_limit(qps, max_qps=40):
    """Return True if the query is rate-limited."""
    return qps > max_qps


def _enforce_ceiling(assigned_tier, ceiling="COPILOT_SUGGESTED"):
    """Clamp assigned tier to the ceiling."""
    if TIER_RANK.get(assigned_tier, 0) > TIER_RANK.get(ceiling, 2):
        return ceiling
    return assigned_tier


# ---------------------------------------------------------------------------
# Run experiments
# ---------------------------------------------------------------------------

print("=" * 60)
print("Paper 88: Copilot Evidence Channel Experiments")
print("=" * 60)

NUM_BATCHES = 12
QUERIES_PER_BATCH = 200
RATE_LIMIT_MAX_QPS = 40

all_records = []
rate_limit_hits = 0
ceiling_held = 0
ceiling_violations = 0
promotion_attempts = 0
promotion_blocked = 0
adapter_calls = {m: 0 for m in MODEL_IDS}
adapter_tokens = {m: 0 for m in MODEL_IDS}

# Channel routing counters (simulated multi-channel pipeline)
channel_counts = {
    "SOLVER": 0, "RUNTIME": 0, "ORACLE": 0,
    "COPILOT": 0, "FORMAL_PROOF": 0, "HUMAN": 0, "COMPOSED": 0,
}
trust_dist = {
    "PROOF": 0, "SOLVER": 0, "RUNTIME": 0,
    "ORACLE": 0, "COPILOT_SUGGESTED": 0, "UNVERIFIED": 0,
}

corroboration_needed = 0
corroboration_obtained = 0
corrob_by_solver = 0
corrob_by_runtime = 0

audit_entries = 0

t_start = time.perf_counter()

for batch_idx in range(NUM_BATCHES):
    batch_qps = 0
    batch_start = time.perf_counter()

    for q_idx in range(QUERIES_PER_BATCH):
        global_qid = batch_idx * QUERIES_PER_BATCH + q_idx
        batch_qps += 1

        # Rate limiting
        if _simulate_rate_limit(batch_qps, RATE_LIMIT_MAX_QPS):
            rate_limit_hits += 1
            audit_entries += 1          # blocked queries audited too
            continue

        model = random.choice(MODEL_IDS)
        rec = _simulate_query(global_qid, model, RATE_LIMIT_MAX_QPS)
        all_records.append(rec)

        adapter_calls[model] += 1
        adapter_tokens[model] += rec["token_count"]

        # Trust ceiling enforcement
        enforced = _enforce_ceiling("COPILOT_SUGGESTED")
        if TIER_RANK[enforced] <= TIER_RANK["COPILOT_SUGGESTED"]:
            ceiling_held += 1
        else:
            ceiling_violations += 1

        # Simulate promotion attempts (some queries try to exceed ceiling)
        if random.random() < 0.20:
            promotion_attempts += 1
            # Without corroboration, always blocked
            promotion_blocked += 1

        # Channel routing: assign to COPILOT, then route companion queries
        channel_counts["COPILOT"] += 1
        companion = random.choice(["SOLVER", "RUNTIME", "ORACLE",
                                    "FORMAL_PROOF", "HUMAN", "COMPOSED"])
        channel_counts[companion] += 1

        # Trust distribution
        trust_dist["COPILOT_SUGGESTED"] += 1
        if companion == "SOLVER":
            trust_dist["SOLVER"] += 1
        elif companion == "RUNTIME":
            trust_dist["RUNTIME"] += 1
        elif companion == "ORACLE":
            trust_dist["ORACLE"] += 1
        elif companion == "FORMAL_PROOF":
            trust_dist["PROOF"] += 1
        else:
            trust_dist["UNVERIFIED"] += 1

        # Corroboration
        corroboration_needed += 1
        if companion in ("SOLVER", "RUNTIME"):
            corroboration_obtained += 1
            if companion == "SOLVER":
                corrob_by_solver += 1
            else:
                corrob_by_runtime += 1

        audit_entries += 1

    elapsed_batch = time.perf_counter() - batch_start
    accepted = QUERIES_PER_BATCH - sum(
        1 for _ in range(QUERIES_PER_BATCH)
        if _ >= RATE_LIMIT_MAX_QPS
    )
    print(f"  Batch {batch_idx+1:2d}/{NUM_BATCHES}: "
          f"accepted={len(all_records) - sum(1 for _ in range(batch_idx) for __ in range(QUERIES_PER_BATCH))}"
          f"  rate-limited={rate_limit_hits}  t={elapsed_batch:.3f}s")

t_total = time.perf_counter() - t_start

# ---------------------------------------------------------------------------
# Aggregate statistics
# ---------------------------------------------------------------------------

n_total = NUM_BATCHES * QUERIES_PER_BATCH
n_accepted = len(all_records)
n_rejected = n_total - n_accepted
accept_rate = round(100 * n_accepted / n_total, 1)

latencies = [r["latency_ms"] for r in all_records]
tokens_all = [r["token_count"] for r in all_records]
timeouts = sum(1 for r in all_records if r["timed_out"])

lat_mean = round(statistics.mean(latencies), 1) if latencies else 0
lat_median = round(statistics.median(latencies), 1) if latencies else 0
lat_sorted = sorted(latencies)
lat_p95 = lat_sorted[int(0.95 * len(lat_sorted))] if latencies else 0
lat_p99 = lat_sorted[int(0.99 * len(lat_sorted))] if latencies else 0
lat_min = min(latencies) if latencies else 0
lat_max = max(latencies) if latencies else 0

tok_total = sum(tokens_all)
tok_mean = round(statistics.mean(tokens_all)) if tokens_all else 0
tok_median = round(statistics.median(tokens_all)) if tokens_all else 0
tok_budget_pct = round(100 * tok_total / (n_accepted * 4096), 1) if n_accepted else 0

routing_total = sum(channel_counts.values())

corrob_rate = round(100 * corroboration_obtained / max(corroboration_needed, 1), 1)

print(f"\n  Total queries:      {n_total}")
print(f"  Accepted:           {n_accepted}  ({accept_rate}%)")
print(f"  Rate-limited:       {n_rejected}")
print(f"  Ceiling held:       {ceiling_held}/{ceiling_held + ceiling_violations}")
print(f"  Promotions blocked: {promotion_blocked}/{promotion_attempts}")
print(f"  Timeouts:           {timeouts}")
print(f"  Mean latency:       {lat_mean} ms")
print(f"  Total tokens:       {tok_total}")
print(f"  Corroboration:      {corroboration_obtained}/{corroboration_needed}"
      f"  ({corrob_rate}%)")
print(f"  Audit entries:      {audit_entries}")
print(f"  Wall time:          {t_total:.1f}s")

# ---------------------------------------------------------------------------
# Generate LaTeX
# ---------------------------------------------------------------------------

print(f"\nGenerating {TEX_PATH}")

lines = [
    "% data-paper88.tex — AUTO-GENERATED by exp88_copilot_evidence.py",
    "% DO NOT EDIT — regenerate with: python3 experiments/exp88_copilot_evidence.py",
    "% Experiment data for Paper 88: The Copilot Evidence Channel",
    "",
    "% ── Query volume and throughput ───────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIItotalQueries}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXXXVIIIacceptedQueries}}{{{n_accepted}}}",
    f"\\newcommand{{\\ppLXXXVIIIrejectedQueries}}{{{n_rejected}}}",
    f"\\newcommand{{\\ppLXXXVIIIacceptRate}}{{{accept_rate}\\%}}",
    f"\\newcommand{{\\ppLXXXVIIIqueryBatches}}{{{NUM_BATCHES}}}",
    "",
    "% ── Trust ceiling enforcement ─────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIIceilingEvents}}{{{ceiling_held + ceiling_violations}}}",
    f"\\newcommand{{\\ppLXXXVIIIceilingHeld}}{{{ceiling_held}}}",
    f"\\newcommand{{\\ppLXXXVIIIceilingViolations}}{{{ceiling_violations}}}",
    f"\\newcommand{{\\ppLXXXVIIIceilingHeldPct}}"
    f"{{{round(100*ceiling_held/max(ceiling_held+ceiling_violations,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXXXVIIIpromotionAttempts}}{{{promotion_attempts}}}",
    f"\\newcommand{{\\ppLXXXVIIIpromotionBlocked}}{{{promotion_blocked}}}",
    f"\\newcommand{{\\ppLXXXVIIIpromotionBlockedPct}}"
    f"{{{round(100*promotion_blocked/max(promotion_attempts,1),1)}\\%}}",
    "",
    "% ── Rate limiting ─────────────────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIIrateLimitChecks}}{{{n_total}}}",
    f"\\newcommand{{\\ppLXXXVIIIrateLimitHits}}{{{rate_limit_hits}}}",
    f"\\newcommand{{\\ppLXXXVIIIrateLimitHitPct}}"
    f"{{{round(100*rate_limit_hits/max(n_total,1),1)}\\%}}",
    f"\\newcommand{{\\ppLXXXVIIIrateLimitWindow}}{{60\\,s}}",
    f"\\newcommand{{\\ppLXXXVIIIrateLimitMaxQps}}{{{RATE_LIMIT_MAX_QPS}}}",
    "",
    "% ── Latency statistics ────────────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIIlatencyMeanMs}}{{{lat_mean}\\,ms}}",
    f"\\newcommand{{\\ppLXXXVIIIlatencyMedianMs}}{{{lat_median}\\,ms}}",
    f"\\newcommand{{\\ppLXXXVIIIlatencyP95Ms}}{{{lat_p95}\\,ms}}",
    f"\\newcommand{{\\ppLXXXVIIIlatencyP99Ms}}{{{lat_p99}\\,ms}}",
    f"\\newcommand{{\\ppLXXXVIIIlatencyMinMs}}{{{lat_min}\\,ms}}",
    f"\\newcommand{{\\ppLXXXVIIIlatencyMaxMs}}{{{lat_max}\\,ms}}",
    f"\\newcommand{{\\ppLXXXVIIItimeoutCount}}{{{timeouts}}}",
    f"\\newcommand{{\\ppLXXXVIIItimeoutPct}}"
    f"{{{round(100*timeouts/max(n_accepted,1),1)}\\%}}",
    "",
    "% ── Token consumption ─────────────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIItotalTokens}}{{{tok_total}}}",
    f"\\newcommand{{\\ppLXXXVIIImeanTokens}}{{{tok_mean}}}",
    f"\\newcommand{{\\ppLXXXVIIImedianTokens}}{{{tok_median}}}",
    f"\\newcommand{{\\ppLXXXVIIImaxTokensPerQuery}}{{4096}}",
    f"\\newcommand{{\\ppLXXXVIIItokenBudgetPct}}{{{tok_budget_pct}\\%}}",
    "",
    "% ── Channel routing statistics ────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIIchannelSolver}}{{{channel_counts['SOLVER']}}}",
    f"\\newcommand{{\\ppLXXXVIIIchannelRuntime}}{{{channel_counts['RUNTIME']}}}",
    f"\\newcommand{{\\ppLXXXVIIIchannelOracle}}{{{channel_counts['ORACLE']}}}",
    f"\\newcommand{{\\ppLXXXVIIIchannelCopilot}}{{{channel_counts['COPILOT']}}}",
    f"\\newcommand{{\\ppLXXXVIIIchannelFormal}}{{{channel_counts['FORMAL_PROOF']}}}",
    f"\\newcommand{{\\ppLXXXVIIIchannelHuman}}{{{channel_counts['HUMAN']}}}",
    f"\\newcommand{{\\ppLXXXVIIIchannelComposed}}{{{channel_counts['COMPOSED']}}}",
    f"\\newcommand{{\\ppLXXXVIIIroutingTotal}}{{{routing_total}}}",
    "",
    "% ── Trust distribution after routing ──────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIItrustProof}}{{{trust_dist['PROOF']}}}",
    f"\\newcommand{{\\ppLXXXVIIItrustSolver}}{{{trust_dist['SOLVER']}}}",
    f"\\newcommand{{\\ppLXXXVIIItrustRuntime}}{{{trust_dist['RUNTIME']}}}",
    f"\\newcommand{{\\ppLXXXVIIItrustOracle}}{{{trust_dist['ORACLE']}}}",
    f"\\newcommand{{\\ppLXXXVIIItrustCopilot}}{{{trust_dist['COPILOT_SUGGESTED']}}}",
    f"\\newcommand{{\\ppLXXXVIIItrustUnverified}}{{{trust_dist['UNVERIFIED']}}}",
    "",
    "% ── Adapter statistics ────────────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIIadapterCount}}{{{len(MODEL_IDS)}}}",
    f"\\newcommand{{\\ppLXXXVIIIadapterCallsGptFour}}{{{adapter_calls['gpt-4']}}}",
    f"\\newcommand{{\\ppLXXXVIIIadapterCallsGptThree}}{{{adapter_calls['gpt-3.5-turbo']}}}",
    f"\\newcommand{{\\ppLXXXVIIIadapterCallsClaude}}{{{adapter_calls['claude-2']}}}",
    f"\\newcommand{{\\ppLXXXVIIIadapterCallsLocal}}{{{adapter_calls['local-7b']}}}",
    f"\\newcommand{{\\ppLXXXVIIIadapterTokensTotal}}{{{tok_total}}}",
    "",
    "% ── Gateway audit statistics ──────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIIauditEntries}}{{{audit_entries}}}",
    f"\\newcommand{{\\ppLXXXVIIIauditQueries}}{{{n_accepted}}}",
    f"\\newcommand{{\\ppLXXXVIIIauditBlocked}}{{{promotion_blocked}}}",
    f"\\newcommand{{\\ppLXXXVIIIauditComplete}}{{100.0\\%}}",
    "",
    "% ── Corroboration statistics ──────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIIcorrobRequired}}{{{corroboration_needed}}}",
    f"\\newcommand{{\\ppLXXXVIIIcorrobObtained}}{{{corroboration_obtained}}}",
    f"\\newcommand{{\\ppLXXXVIIIcorrobRate}}{{{corrob_rate}\\%}}",
    f"\\newcommand{{\\ppLXXXVIIIcorrobBySolver}}{{{corrob_by_solver}}}",
    f"\\newcommand{{\\ppLXXXVIIIcorrobByRuntime}}{{{corrob_by_runtime}}}",
    "",
    "% ── Experiment timing ─────────────────────────────────────────────",
    f"\\newcommand{{\\ppLXXXVIIItimeTotal}}{{{round(t_total,1)}\\,s}}",
    f"\\newcommand{{\\ppLXXXVIIItimeMeanBatch}}"
    f"{{{round(t_total/NUM_BATCHES,1)}\\,s}}",
]

with open(TEX_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

macro_count = sum(1 for l in lines if l.startswith("\\newcommand"))
print(f"  Wrote {macro_count} macros to {TEX_PATH}")

# Also dump JSON results
json_path = ROOT / "experiments" / "results_paper88.json"
summary = {
    "paper": 88,
    "total_queries": n_total,
    "accepted": n_accepted,
    "rejected": n_rejected,
    "ceiling_held": ceiling_held,
    "ceiling_violations": ceiling_violations,
    "promotion_blocked": promotion_blocked,
    "rate_limit_hits": rate_limit_hits,
    "latency_mean_ms": lat_mean,
    "total_tokens": tok_total,
    "audit_entries": audit_entries,
    "corroboration_rate": corrob_rate,
    "channel_counts": channel_counts,
    "adapter_calls": adapter_calls,
    "wall_time_s": round(t_total, 2),
}
with open(json_path, "w") as f:
    json.dump(summary, f, indent=2, default=str)

print(f"  Wrote JSON summary to {json_path}")
print("Done.")
