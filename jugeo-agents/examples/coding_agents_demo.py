#!/usr/bin/env python3
"""Cross-Agent Code Verification — Claude Code vs Copilot CLI vs Codex.

Demonstrates jugeo-agents as the verification layer sitting ON TOP of all
three major coding agents, using sheaf theory to detect contradictions
and assemble the best composite answer.

Scenario: All three agents implement a rate limiter
---------------------------------------------------
- Claude Code:  Token-bucket algorithm, O(1) per request, tests pass ✓
- Copilot CLI:  Sliding window, O(n) per request, no tests
- Codex:        Fixed window, claims O(1) but has edge-case bug

JuGeo detects:
1. Complexity contradiction: Claude says O(1), Codex says O(1) but
   Copilot says O(n) — H¹ obstruction
2. Claude's test evidence gives it highest trust
3. Codex's claim is ungrounded (no test evidence)

Run it:
    pip install jugeo-agents[nlp]
    python examples/coding_agents_demo.py
"""

from jugeo_agents.adapters.coding_agents import (
    ClaudeCodeAdapter,
    CopilotCLIAdapter,
    CodexAdapter,
    CodingAgentOrchestrator,
)


def main() -> None:
    print("=" * 70)
    print("  CROSS-AGENT CODE VERIFICATION")
    print("  Claude Code  ×  Copilot CLI  ×  Codex")
    print("  Sheaf-theoretic fusion of coding agent outputs")
    print("=" * 70)
    print()

    orch = CodingAgentOrchestrator()

    # ── Claude Code: token-bucket, O(1), tests pass ──────────
    print("▶ Claude Code — token-bucket rate limiter, tests pass")
    orch.add_output(ClaudeCodeAdapter.from_response(
        code='''
class TokenBucketLimiter:
    """Rate limiter using token bucket algorithm.
    O(1) time per request. Thread-safe with atomic operations.
    Handles burst traffic gracefully.
    """
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()

    def allow(self) -> bool:
        """Check if request is allowed. O(1) amortized."""
        self._refill()
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now
''',
        explanation=(
            "Implemented a token bucket rate limiter with O(1) amortized time "
            "complexity per request. Uses the token bucket pattern for smooth "
            "rate limiting that handles burst traffic. Thread-safe with atomic "
            "refill operations. All 18 tests pass including edge cases for "
            "burst handling and zero-rate scenarios."
        ),
        tools_used=["bash:pytest", "bash:python -m mypy", "read_file:requirements.txt"],
        test_results={"passed": True, "count": 18, "exit_code": 0},
        files_modified=["src/rate_limiter.py", "tests/test_rate_limiter.py"],
        model="claude-sonnet-4",
    ))

    # ── Copilot CLI: sliding window, O(n) ────────────────────
    print("▶ Copilot CLI — sliding window rate limiter, no tests")
    orch.add_output(CopilotCLIAdapter.from_response(
        code='''
class SlidingWindowLimiter:
    """Rate limiter using sliding window log.
    Tracks each request timestamp for precise limiting.
    """
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window = window_seconds
        self._log: list[float] = []

    def allow(self) -> bool:
        """Check if request is allowed. O(n) where n = window size."""
        now = time.monotonic()
        # Remove expired entries — O(n) scan
        self._log = [t for t in self._log if now - t < self.window]
        if len(self._log) < self.max_requests:
            self._log.append(now)
            return True
        return False
''',
        explanation=(
            "Implemented a sliding window log rate limiter. Time complexity "
            "is O(n) per request where n is the number of requests in the "
            "current window, because we scan the entire log to remove expired "
            "entries. More precise than fixed-window but slower under load. "
            "Does not handle burst traffic as smoothly as token bucket."
        ),
        model="gpt-4.1",
        # No tools_used, no test_results — pure generation
    ))

    # ── Codex: fixed window, claims O(1) but has bug ─────────
    print("▶ Codex — fixed window rate limiter, claims O(1)")
    orch.add_output(CodexAdapter.from_response(
        code='''
class FixedWindowLimiter:
    """Rate limiter using fixed time windows.
    O(1) per request with simple counter reset.
    """
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window = window_seconds
        self._count = 0
        self._window_start = time.monotonic()

    def allow(self) -> bool:
        """Check if request allowed. O(1) time."""
        now = time.monotonic()
        if now - self._window_start >= self.window:
            self._count = 0
            self._window_start = now
        if self._count < self.max_requests:
            self._count += 1
            return True
        return False
''',
        explanation=(
            "Implemented a fixed window rate limiter with O(1) time complexity "
            "per request. Uses a simple counter that resets at window boundaries. "
            "Handles burst traffic by allowing max_requests at window edges. "
            "The implementation is production-ready and handles all edge cases."
        ),
        model="o4-mini",
        # No tools_used — pure generation, claims are unverified
    ))

    # ── VERIFY ────────────────────────────────────────────────
    print()
    print("━" * 70)
    print("  RUNNING SHEAF-THEORETIC VERIFICATION...")
    print("━" * 70)
    print()

    report = orch.full_report()
    section = report.global_section

    print(section.summary_text())

    # ── Trust Leaderboard ─────────────────────────────────────
    print()
    print("━" * 70)
    print("  AGENT TRUST LEADERBOARD")
    print("━" * 70)
    sorted_agents = sorted(
        section.agent_trust_scores.items(),
        key=lambda x: x[1],
        reverse=True,
    )
    for rank, (agent, score) in enumerate(sorted_agents, 1):
        bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        trust_label = {
            "claude-code": "TOOL_VERIFIED (tests pass)",
            "copilot-cli": "WEAK_MODEL_GENERATED",
            "codex": "WEAK_MODEL_GENERATED",
        }.get(agent, "")
        print(f"  {rank}. {agent:15s} [{bar}] {score:.0%}  {trust_label}")

    # ── Naive comparison ──────────────────────────────────────
    if report.naive_comparison:
        print()
        print("━" * 70)
        print("  JuGeo vs NAIVE APPROACH")
        print("━" * 70)
        print(report.advantage_text())

    # ── Key insight ───────────────────────────────────────────
    print()
    print("═" * 70)
    print("  KEY INSIGHT")
    print("─" * 70)
    print("  Claude Code's output is TOOL_VERIFIED (pytest ran, 18 tests pass).")
    print("  Copilot CLI and Codex are WEAK_MODEL_GENERATED (no tool evidence).")
    print("  When claims contradict, the trust algebra picks the tool-verified")
    print("  agent — not the majority. This is why sheaf theory beats voting.")
    print()
    print("  In the presheaf model:")
    print("    • Each agent's output = local section σᵢ over the codebase")
    print("    • Overlapping claims = restriction to U_i ∩ U_j")
    print("    • Contradictions = H¹ obstructions (failure of sheaf condition)")
    print("    • Resolution = trust-weighted global section assembly")
    print("═" * 70)


if __name__ == "__main__":
    main()
