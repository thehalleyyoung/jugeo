"""Tests for jugeo.se_theory.teams (B9 — Teams / Jurisdiction).

Covers:
* CodeownersParser  — parsing, pattern matching, owner resolution
* JurisdictionManager — assignment, authority resolution, delegation,
                        conflict detection and resolution suggestions
* EscalationRouter  — routing, escalation level computation, escalating,
                      auto-assign
* TreatyNegotiator  — proposing treaties, compliance checks, renegotiation,
                      stale-treaty detection
* JurisdictionReporter — report generation, coverage stats, workload
* Integration helpers — CodeownersIntegrator, AuthorityIntegrator
"""
from __future__ import annotations

import time

import pytest

from jugeo.se_theory.teams.models import (
    AuthorityGrant,
    AuthorityLevel,
    CodeownersEntry,
    CodeownersMapping,
    CrossTeamTreaty,
    EscalationLevel,
    Jurisdiction,
    ObstructionEscalation,
    Team,
    TeamRole,
    _iso_now,
    authority_rank,
    weaker_authority,
)
from jugeo.se_theory.teams.algorithms import (
    CodeownersParser,
    EscalationRouter,
    JurisdictionManager,
    JurisdictionReporter,
    TreatyNegotiator,
    TRUST_ORDER,
    trust_rank,
)
from jugeo.se_theory.teams.integration import (
    AuthorityIntegrator,
    CodeownersIntegrator,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CODEOWNERS = """\
# This is a CODEOWNERS file
* @org/everyone
/src/core/ @org/platform
/src/core/auth.py @org/security @org/platform
/docs/ @org/docs-team
*.md @org/docs-team
/tests/ @org/qa
"""


@pytest.fixture
def parser() -> CodeownersParser:
    return CodeownersParser()


@pytest.fixture
def sample_mapping(parser: CodeownersParser) -> CodeownersMapping:
    return parser.parse_codeowners(SAMPLE_CODEOWNERS)


@pytest.fixture
def teams() -> list[Team]:
    platform = Team(
        id="platform",
        name="Platform Team",
        members=["alice", "bob"],
        authority_level=AuthorityLevel.FULL,
        coordinate_patterns=["src/core/*", "src/infra/*"],
        trust_ceiling="verified",
    )
    security = Team(
        id="security",
        name="Security Team",
        members=["carol"],
        authority_level=AuthorityLevel.APPROVE,
        coordinate_patterns=["src/core/auth*"],
        trust_ceiling="proof",
        parent_team_id="platform",
    )
    qa = Team(
        id="qa",
        name="QA Team",
        members=["dave"],
        authority_level=AuthorityLevel.REVIEW_ONLY,
        coordinate_patterns=["tests/*"],
        trust_ceiling="heuristic",
    )
    return [platform, security, qa]


@pytest.fixture
def coordinates() -> list[str]:
    return [
        "src/core/main.py",
        "src/core/auth.py",
        "src/infra/deploy.py",
        "tests/test_core.py",
        "docs/readme.md",
    ]


@pytest.fixture
def jurisdictions(teams, coordinates) -> dict[str, list[Jurisdiction]]:
    mgr = JurisdictionManager()
    return mgr.assign_jurisdictions(teams, coordinates)


# ---------------------------------------------------------------------------
# Model serialisation round-trips
# ---------------------------------------------------------------------------


class TestTeamModel:
    def test_to_from_dict_round_trip(self):
        t = Team(
            id="eng",
            name="Engineering",
            members=["alice", "bob"],
            authority_level=AuthorityLevel.FULL,
            coordinate_patterns=["src/**"],
            trust_ceiling="verified",
            parent_team_id="org",
            metadata={"slack": "#eng"},
        )
        d = t.to_dict()
        t2 = Team.from_dict(d)
        assert t2.id == "eng"
        assert t2.name == "Engineering"
        assert t2.members == ["alice", "bob"]
        assert t2.authority_level == AuthorityLevel.FULL
        assert t2.coordinate_patterns == ["src/**"]
        assert t2.trust_ceiling == "verified"
        assert t2.parent_team_id == "org"
        assert t2.metadata == {"slack": "#eng"}

    def test_default_authority_level(self):
        t = Team(id="x", name="X")
        assert t.authority_level == AuthorityLevel.APPROVE

    def test_authority_rank_ordering(self):
        assert authority_rank(AuthorityLevel.READ_ONLY) < authority_rank(AuthorityLevel.REVIEW_ONLY)
        assert authority_rank(AuthorityLevel.REVIEW_ONLY) < authority_rank(AuthorityLevel.APPROVE)
        assert authority_rank(AuthorityLevel.APPROVE) < authority_rank(AuthorityLevel.FULL)

    def test_weaker_authority(self):
        assert weaker_authority(AuthorityLevel.FULL, AuthorityLevel.APPROVE) == AuthorityLevel.APPROVE
        assert weaker_authority(AuthorityLevel.READ_ONLY, AuthorityLevel.FULL) == AuthorityLevel.READ_ONLY


class TestJurisdictionModel:
    def test_to_from_dict_round_trip(self):
        j = Jurisdiction(
            team_id="platform",
            coordinate_pattern="src/core/*",
            authority=AuthorityLevel.FULL,
            trust_ceiling="verified",
            delegated_from=None,
            delegation_depth=0,
            conditions={"requires_tests": True},
        )
        d = j.to_dict()
        j2 = Jurisdiction.from_dict(d)
        assert j2.team_id == "platform"
        assert j2.authority == AuthorityLevel.FULL
        assert j2.conditions == {"requires_tests": True}
        assert j2.delegation_depth == 0

    def test_delegation_depth_preserved(self):
        j = Jurisdiction(team_id="sec", delegation_depth=3)
        assert Jurisdiction.from_dict(j.to_dict()).delegation_depth == 3


class TestObstructionEscalation:
    def test_to_from_dict_round_trip(self):
        obs = ObstructionEscalation(
            obstruction_id="obs-1",
            coordinate_id="src/core/auth.py",
            blast_radius=5,
            escalation_level=EscalationLevel.DEPARTMENT,
            responsible_team="platform",
            escalation_chain=["platform", "org"],
            status="OPEN",
        )
        d = obs.to_dict()
        obs2 = ObstructionEscalation.from_dict(d)
        assert obs2.obstruction_id == "obs-1"
        assert obs2.blast_radius == 5
        assert obs2.escalation_level == EscalationLevel.DEPARTMENT
        assert obs2.responsible_team == "platform"
        assert obs2.escalation_chain == ["platform", "org"]


class TestCrossTeamTreaty:
    def test_to_from_dict_round_trip(self):
        t = CrossTeamTreaty(
            treaty_id="t-1",
            team_a="platform",
            team_b="security",
            overlap_coordinates=["src/core/auth.py"],
            agreed_propositions=["auth interface stable"],
            trust_floor="proof",
            review_policy="dual approval",
            status="ACTIVE",
        )
        d = t.to_dict()
        t2 = CrossTeamTreaty.from_dict(d)
        assert t2.treaty_id == "t-1"
        assert t2.trust_floor == "proof"
        assert t2.status == "ACTIVE"


class TestAuthorityGrant:
    def test_to_from_dict_round_trip(self):
        g = AuthorityGrant(
            granting_team="platform",
            receiving_team="security",
            coordinate_patterns=["src/core/auth*"],
            authority=AuthorityLevel.APPROVE,
            trust_attenuation="proof",
            valid_from="2024-01-01T00:00:00Z",
            conditions={"min_reviewers": 2},
        )
        d = g.to_dict()
        g2 = AuthorityGrant.from_dict(d)
        assert g2.granting_team == "platform"
        assert g2.receiving_team == "security"
        assert g2.authority == AuthorityLevel.APPROVE
        assert g2.conditions == {"min_reviewers": 2}


# ---------------------------------------------------------------------------
# CodeownersParser
# ---------------------------------------------------------------------------


class TestCodeownersParser:
    def test_parse_skips_comments_and_blanks(self, parser, sample_mapping):
        # Should not include comment lines
        for entry in sample_mapping.entries:
            assert not entry.pattern.startswith("#")

    def test_parse_extracts_teams(self, parser, sample_mapping):
        # The wildcard rule should resolve to org/everyone
        wildcard = next(
            (e for e in sample_mapping.entries if e.pattern == "*"), None
        )
        assert wildcard is not None
        assert "org/everyone" in wildcard.teams

    def test_parse_strips_at_sign(self, parser):
        content = "/src/ @myteam @otherteam"
        mapping = parser.parse_codeowners(content)
        assert len(mapping.entries) == 1
        assert "myteam" in mapping.entries[0].teams
        assert "otherteam" in mapping.entries[0].teams

    def test_priority_more_specific_higher(self, parser):
        generic = parser._priority_score("*")
        specific = parser._priority_score("/src/core/auth.py")
        assert specific > generic

    def test_priority_doublestar_lower_than_literal(self, parser):
        dstar = parser._priority_score("**/*.py")
        literal = parser._priority_score("/src/core/module.py")
        assert literal > dstar

    def test_resolve_owner_most_specific_wins(self, parser, sample_mapping):
        # auth.py should be owned by security+platform, not just org/everyone
        owners = parser.resolve_owner("src/core/auth.py", sample_mapping)
        # The most specific entry for auth.py has security + platform
        assert "org/security" in owners or "org/platform" in owners

    def test_resolve_owner_fallback_to_wildcard(self, parser, sample_mapping):
        owners = parser.resolve_owner("unknown/file.txt", sample_mapping)
        assert "org/everyone" in owners

    def test_resolve_owner_no_match_returns_empty(self, parser):
        mapping = CodeownersMapping(entries=[])
        assert parser.resolve_owner("src/foo.py", mapping) == []

    def test_pattern_to_regex_doublestar(self, parser):
        regex = parser._pattern_to_regex("**/*.py")
        import re
        assert re.match(regex, "src/core/module.py")
        assert re.match(regex, "a/b/c/d.py")

    def test_matches_directory_prefix(self, parser):
        assert parser._matches("src/core/main.py", "src/core/")
        assert not parser._matches("lib/main.py", "src/core/")

    def test_matches_basename_pattern(self, parser):
        assert parser._matches("docs/readme.md", "*.md")
        assert parser._matches("README.md", "*.md")

    def test_parse_returns_sorted_by_priority(self, parser, sample_mapping):
        priorities = [e.priority for e in sample_mapping.entries]
        assert priorities == sorted(priorities)


# ---------------------------------------------------------------------------
# JurisdictionManager
# ---------------------------------------------------------------------------


class TestJurisdictionManager:
    def test_assign_covers_known_coords(self, jurisdictions, coordinates):
        for coord in ["src/core/main.py", "src/core/auth.py", "tests/test_core.py"]:
            assert coord in jurisdictions

    def test_platform_owns_core_main(self, jurisdictions):
        jlist = jurisdictions.get("src/core/main.py", [])
        team_ids = [j.team_id for j in jlist]
        assert "platform" in team_ids

    def test_security_owns_auth(self, jurisdictions):
        jlist = jurisdictions.get("src/core/auth.py", [])
        team_ids = [j.team_id for j in jlist]
        assert "security" in team_ids

    def test_qa_owns_tests(self, jurisdictions):
        jlist = jurisdictions.get("tests/test_core.py", [])
        team_ids = [j.team_id for j in jlist]
        assert "qa" in team_ids

    def test_uncovered_coord_returns_empty_list(self, jurisdictions):
        assert jurisdictions.get("docs/readme.md", []) == []

    def test_resolve_authority_returns_highest(self, jurisdictions):
        mgr = JurisdictionManager()
        # auth.py is covered by both security (APPROVE) and platform (FULL)
        winner = mgr.resolve_authority("src/core/auth.py", jurisdictions)
        assert winner is not None
        assert winner.team_id == "platform"  # FULL > APPROVE

    def test_resolve_authority_uncovered_returns_none(self, jurisdictions):
        mgr = JurisdictionManager()
        result = mgr.resolve_authority("docs/readme.md", jurisdictions)
        assert result is None

    def test_detect_conflicts_finds_overlapping_teams(self, jurisdictions):
        mgr = JurisdictionManager()
        conflicts = mgr.detect_conflicts(jurisdictions)
        # auth.py is owned by platform (FULL) and security (APPROVE) — different ranks, no conflict
        # To make a conflict we inject a coord covered by two FULL teams
        extra = dict(jurisdictions)
        extra["contested/coord.py"] = [
            Jurisdiction(
                team_id="team-a",
                coordinate_pattern="contested/*",
                authority=AuthorityLevel.FULL,
            ),
            Jurisdiction(
                team_id="team-b",
                coordinate_pattern="contested/*",
                authority=AuthorityLevel.FULL,
            ),
        ]
        conflicts2 = mgr.detect_conflicts(extra)
        conflict_coords = [c for c, _ in conflicts2]
        assert "contested/coord.py" in conflict_coords

    def test_suggest_resolutions_preferred_team_has_lower_depth(self):
        mgr = JurisdictionManager()
        conflicts = [
            (
                "src/ambiguous.py",
                [
                    Jurisdiction(
                        team_id="primary", authority=AuthorityLevel.FULL, delegation_depth=0
                    ),
                    Jurisdiction(
                        team_id="delegatee", authority=AuthorityLevel.FULL, delegation_depth=1
                    ),
                ],
            )
        ]
        resolutions = mgr.suggest_resolutions(conflicts)
        assert len(resolutions) == 1
        assert resolutions[0]["preferred_team"] == "primary"

    def test_compute_trust_ceiling_attenuation(self):
        mgr = JurisdictionManager()
        chain = [
            Jurisdiction(trust_ceiling="verified"),
            Jurisdiction(trust_ceiling="proof"),
            Jurisdiction(trust_ceiling="heuristic"),
        ]
        ceiling = mgr.compute_trust_ceiling("coord", chain)
        assert ceiling == "heuristic"

    def test_compute_trust_ceiling_empty_chain(self):
        mgr = JurisdictionManager()
        assert mgr.compute_trust_ceiling("coord", []) == "none"

    def test_check_delegation_chain_valid(self, teams, coordinates):
        mgr = JurisdictionManager()
        jurs = mgr.assign_jurisdictions(teams, coordinates)
        grant = AuthorityGrant(
            granting_team="platform",
            receiving_team="security",
            coordinate_patterns=["src/core/auth.py"],
            authority=AuthorityLevel.APPROVE,
        )
        assert mgr.check_delegation_chain(grant, jurs) is True

    def test_check_delegation_chain_invalid_insufficient_authority(self, teams, coordinates):
        mgr = JurisdictionManager()
        jurs = mgr.assign_jurisdictions(teams, coordinates)
        # QA team only has REVIEW_ONLY — cannot grant FULL
        grant = AuthorityGrant(
            granting_team="qa",
            receiving_team="platform",
            coordinate_patterns=["tests/test_core.py"],
            authority=AuthorityLevel.FULL,
        )
        assert mgr.check_delegation_chain(grant, jurs) is False


# ---------------------------------------------------------------------------
# EscalationRouter
# ---------------------------------------------------------------------------


class TestEscalationRouter:
    def test_route_obstruction_creates_record(self, jurisdictions):
        router = EscalationRouter()
        obs = router.route_obstruction(
            obstruction_id="ob-1",
            coordinate_id="src/core/auth.py",
            blast_radius=3,
            jurisdictions=jurisdictions,
        )
        assert obs.obstruction_id == "ob-1"
        assert obs.coordinate_id == "src/core/auth.py"
        assert obs.blast_radius == 3
        assert obs.status == "OPEN"
        assert obs.responsible_team  # some team assigned
        assert obs.escalation_level == EscalationLevel.TEAM

    def test_compute_escalation_level_team(self):
        router = EscalationRouter()
        assert router.compute_escalation_level(2, False) == EscalationLevel.TEAM

    def test_compute_escalation_level_department(self):
        router = EscalationRouter()
        assert router.compute_escalation_level(10, False) == EscalationLevel.DEPARTMENT

    def test_compute_escalation_level_organization(self):
        router = EscalationRouter()
        assert router.compute_escalation_level(25, False) == EscalationLevel.ORGANIZATION

    def test_compute_escalation_level_emergency_critical_path(self):
        router = EscalationRouter()
        assert (
            router.compute_escalation_level(10, True) == EscalationLevel.EMERGENCY
        )

    def test_compute_escalation_level_organization_critical_not_large(self):
        router = EscalationRouter()
        # Critical path but blast_radius < 10 → ORGANIZATION (not EMERGENCY)
        assert (
            router.compute_escalation_level(5, True) == EscalationLevel.ORGANIZATION
        )

    def test_build_escalation_chain_non_empty(self, jurisdictions):
        router = EscalationRouter()
        chain = router.build_escalation_chain("src/core/auth.py", jurisdictions)
        assert len(chain) >= 1

    def test_build_escalation_chain_empty_for_uncovered(self, jurisdictions):
        router = EscalationRouter()
        chain = router.build_escalation_chain("docs/readme.md", jurisdictions)
        assert chain == []

    def test_build_escalation_chain_includes_parent(self, teams, jurisdictions):
        router = EscalationRouter()
        teams_by_id = {t.id: t for t in teams}
        chain = router.build_escalation_chain(
            "src/core/auth.py", jurisdictions, teams_by_id
        )
        # security has parent_team_id="platform"
        assert "platform" in chain

    def test_escalate_advances_level(self, jurisdictions):
        router = EscalationRouter()
        obs = router.route_obstruction(
            obstruction_id="ob-2",
            coordinate_id="src/core/main.py",
            blast_radius=2,
            jurisdictions=jurisdictions,
        )
        escalated = router.escalate(obs, EscalationLevel.DEPARTMENT)
        assert escalated.escalation_level == EscalationLevel.DEPARTMENT
        assert escalated.status == "ACKNOWLEDGED"
        assert escalated.acknowledged_at is not None

    def test_auto_assign_picks_least_loaded(self):
        router = EscalationRouter()
        obs = ObstructionEscalation(
            obstruction_id="ob-3",
            coordinate_id="coord",
            escalation_chain=["team-a", "team-b", "team-c"],
            responsible_team="team-a",
        )
        workload = {"team-a": 5, "team-b": 2, "team-c": 8}
        assigned = router.auto_assign(obs, workload)
        assert assigned == "team-b"

    def test_auto_assign_empty_chain_returns_responsible(self):
        router = EscalationRouter()
        obs = ObstructionEscalation(
            obstruction_id="ob-4",
            coordinate_id="coord",
            escalation_chain=[],
            responsible_team="fallback-team",
        )
        assert router.auto_assign(obs, {}) == "fallback-team"


# ---------------------------------------------------------------------------
# TreatyNegotiator
# ---------------------------------------------------------------------------


class TestTreatyNegotiator:
    def test_propose_treaty_pending_status(self, teams):
        neg = TreatyNegotiator()
        platform, security, _ = teams
        treaty = neg.propose_treaty(
            team_a=platform,
            team_b=security,
            overlap_coords=["src/core/auth.py"],
        )
        assert treaty.status == "PENDING"
        assert treaty.team_a == "platform"
        assert treaty.team_b == "security"
        assert "src/core/auth.py" in treaty.overlap_coordinates

    def test_propose_treaty_contains_default_proposition(self, teams):
        neg = TreatyNegotiator()
        platform, security, _ = teams
        treaty = neg.propose_treaty(
            team_a=platform,
            team_b=security,
            overlap_coords=["src/core/auth.py"],
            sections=[],
        )
        # Should have a default proposition about interface compatibility
        assert any("interface compatibility" in p for p in treaty.agreed_propositions)

    def test_propose_treaty_extracts_section_propositions(self, teams):
        neg = TreatyNegotiator()
        platform, security, _ = teams
        sections = [
            {
                "coordinate_id": "src/core/auth.py",
                "proposition": "auth returns valid JWT",
            }
        ]
        treaty = neg.propose_treaty(
            team_a=platform,
            team_b=security,
            overlap_coords=["src/core/auth.py"],
            sections=sections,
        )
        assert "auth returns valid JWT" in treaty.agreed_propositions

    def test_propose_treaty_dual_approval_policy_for_two_full_teams(self, teams):
        neg = TreatyNegotiator()
        platform = teams[0]
        another_full = Team(
            id="another",
            authority_level=AuthorityLevel.FULL,
        )
        treaty = neg.propose_treaty(
            team_a=platform,
            team_b=another_full,
            overlap_coords=["shared/coord.py"],
        )
        assert treaty.review_policy == "dual approval"

    def test_check_treaty_compliance_no_violations_when_evidenced(self, teams):
        neg = TreatyNegotiator()
        platform, security, _ = teams
        treaty = CrossTeamTreaty(
            team_a="platform",
            team_b="security",
            overlap_coordinates=["src/core/auth.py"],
            agreed_propositions=["auth is stable"],
            trust_floor="heuristic",
            status="ACTIVE",
        )
        sections = [
            {
                "coordinate_id": "src/core/auth.py",
                "proposition": "auth is stable",
                "trust_level": "proof",
            }
        ]
        violations = neg.check_treaty_compliance(treaty, sections)
        assert violations == []

    def test_check_treaty_compliance_missing_proposition(self, teams):
        neg = TreatyNegotiator()
        treaty = CrossTeamTreaty(
            team_a="platform",
            team_b="security",
            overlap_coordinates=["src/core/auth.py"],
            agreed_propositions=["auth is stable", "no SQL injection"],
            trust_floor="heuristic",
            status="ACTIVE",
        )
        sections = [
            {
                "coordinate_id": "src/core/auth.py",
                "proposition": "auth is stable",
                "trust_level": "proof",
            }
        ]
        violations = neg.check_treaty_compliance(treaty, sections)
        assert any("no SQL injection" in v for v in violations)

    def test_check_treaty_compliance_trust_below_floor(self, teams):
        neg = TreatyNegotiator()
        treaty = CrossTeamTreaty(
            team_a="platform",
            team_b="security",
            overlap_coordinates=["src/core/auth.py"],
            agreed_propositions=["auth is stable"],
            trust_floor="proof",
            status="ACTIVE",
        )
        sections = [
            {
                "coordinate_id": "src/core/auth.py",
                "proposition": "auth is stable",
                "trust_level": "claim",  # below proof
            }
        ]
        violations = neg.check_treaty_compliance(treaty, sections)
        assert any("trust" in v.lower() for v in violations)

    def test_renegotiate_updates_overlap_and_timestamp(self, teams):
        neg = TreatyNegotiator()
        platform, security, _ = teams
        original = neg.propose_treaty(
            team_a=platform,
            team_b=security,
            overlap_coords=["src/core/auth.py"],
        )
        renegotiated = neg.renegotiate(
            original, changed_coords=["src/core/permissions.py"]
        )
        assert "src/core/permissions.py" in renegotiated.overlap_coordinates
        assert "src/core/auth.py" in renegotiated.overlap_coordinates
        assert renegotiated.treaty_id == original.treaty_id

    def test_expire_stale_treaties_detects_old_treaty(self):
        neg = TreatyNegotiator()
        old_treaty = CrossTeamTreaty(
            treaty_id="old",
            team_a="a",
            team_b="b",
            last_negotiated="2000-01-01T00:00:00Z",
            status="ACTIVE",
        )
        fresh_treaty = CrossTeamTreaty(
            treaty_id="fresh",
            team_a="a",
            team_b="b",
            last_negotiated=_iso_now(),
            status="ACTIVE",
        )
        stale = neg.expire_stale_treaties(
            [old_treaty, fresh_treaty], threshold_days=30
        )
        assert "old" in stale
        assert "fresh" not in stale


# ---------------------------------------------------------------------------
# JurisdictionReporter
# ---------------------------------------------------------------------------


class TestJurisdictionReporter:
    def test_generate_report_basic_counts(self, teams, jurisdictions):
        reporter = JurisdictionReporter()
        report = reporter.generate_report(
            teams=teams,
            jurisdictions=jurisdictions,
        )
        assert report.total_coordinates == len(jurisdictions)
        assert report.covered_by_team <= report.total_coordinates
        assert report.uncovered >= 0

    def test_coverage_stats_pct(self, jurisdictions):
        reporter = JurisdictionReporter()
        stats = reporter.coverage_stats(jurisdictions)
        assert 0.0 <= stats["coverage_pct"] <= 100.0
        assert stats["total"] == len(jurisdictions)
        assert stats["covered"] + stats["uncovered"] == stats["total"]

    def test_team_workload_counts_open_only(self, teams):
        reporter = JurisdictionReporter()
        obs_open = ObstructionEscalation(
            obstruction_id="o1",
            coordinate_id="x",
            responsible_team="platform",
            status="OPEN",
        )
        obs_resolved = ObstructionEscalation(
            obstruction_id="o2",
            coordinate_id="y",
            responsible_team="platform",
            status="RESOLVED",
        )
        workload = reporter.team_workload(teams, [obs_open, obs_resolved])
        assert workload["platform"] == 1

    def test_generate_report_includes_pending_treaties(self, teams, jurisdictions):
        reporter = JurisdictionReporter()
        treaty = CrossTeamTreaty(
            treaty_id="t1",
            team_a="platform",
            team_b="security",
            status="PENDING",
        )
        report = reporter.generate_report(
            teams=teams,
            jurisdictions=jurisdictions,
            treaties=[treaty],
        )
        assert any(t.treaty_id == "t1" for t in report.pending_treaties)

    def test_report_excludes_resolved_escalations(self, teams, jurisdictions):
        reporter = JurisdictionReporter()
        resolved = ObstructionEscalation(
            obstruction_id="o-resolved",
            coordinate_id="src/core/main.py",
            responsible_team="platform",
            status="RESOLVED",
        )
        open_obs = ObstructionEscalation(
            obstruction_id="o-open",
            coordinate_id="src/core/main.py",
            responsible_team="platform",
            status="OPEN",
        )
        report = reporter.generate_report(
            teams=teams,
            jurisdictions=jurisdictions,
            obstructions=[resolved, open_obs],
        )
        ids = [e.obstruction_id for e in report.escalation_queue]
        assert "o-open" in ids
        assert "o-resolved" not in ids


# ---------------------------------------------------------------------------
# CodeownersIntegrator
# ---------------------------------------------------------------------------


class TestCodeownersIntegrator:
    def test_to_jurisdictions_produces_one_per_team_per_entry(self):
        integrator = CodeownersIntegrator()
        mapping = CodeownersMapping(
            entries=[
                CodeownersEntry(
                    pattern="/src/", teams=["team-a", "team-b"], priority=10
                ),
                CodeownersEntry(
                    pattern="/docs/", teams=["docs-team"], priority=5
                ),
            ]
        )
        jurs = integrator.to_jurisdictions(mapping)
        team_ids = [j.team_id for j in jurs]
        assert "team-a" in team_ids
        assert "team-b" in team_ids
        assert "docs-team" in team_ids
        assert len(jurs) == 3

    def test_sync_with_site_coverage(self):
        integrator = CodeownersIntegrator()
        mapping = CodeownersMapping(
            entries=[
                CodeownersEntry(pattern="src/*", teams=["eng"], priority=5),
            ]
        )
        coords = ["src/main.py", "src/utils.py", "tests/test_main.py"]
        result = integrator.sync_with_site(mapping, coords)
        assert "src/main.py" in result["pattern_to_coords"].get("src/*", [])
        assert "tests/test_main.py" in result["unowned_coords"]
        assert result["coverage_pct"] < 100.0

    def test_sync_with_site_unmatched_patterns(self):
        integrator = CodeownersIntegrator()
        mapping = CodeownersMapping(
            entries=[
                CodeownersEntry(
                    pattern="nonexistent/*", teams=["x"], priority=5
                )
            ]
        )
        result = integrator.sync_with_site(mapping, ["src/a.py"])
        assert "nonexistent/*" in result["unmatched_patterns"]


# ---------------------------------------------------------------------------
# AuthorityIntegrator
# ---------------------------------------------------------------------------


class TestAuthorityIntegrator:
    def test_from_authority_grants_produces_jurisdictions(self):
        integrator = AuthorityIntegrator()
        grant = AuthorityGrant(
            granting_team="platform",
            receiving_team="security",
            coordinate_patterns=["src/auth/*"],
            authority=AuthorityLevel.APPROVE,
            trust_attenuation="proof",
            valid_from="2024-01-01T00:00:00Z",
        )
        jurs = integrator.from_authority_grants([grant])
        assert len(jurs) == 1
        assert jurs[0].team_id == "security"
        assert jurs[0].authority == AuthorityLevel.APPROVE
        assert jurs[0].delegated_from == "platform"
        assert jurs[0].delegation_depth == 1

    def test_from_authority_grants_skips_expired(self):
        integrator = AuthorityIntegrator()
        expired_grant = AuthorityGrant(
            granting_team="platform",
            receiving_team="sec",
            coordinate_patterns=["src/*"],
            authority=AuthorityLevel.APPROVE,
            valid_from="2020-01-01T00:00:00Z",
            valid_until="2020-06-01T00:00:00Z",  # in the past
        )
        jurs = integrator.from_authority_grants([expired_grant])
        assert jurs == []

    def test_to_authority_grants_groups_by_team(self):
        integrator = AuthorityIntegrator()
        jurs = [
            Jurisdiction(
                team_id="security",
                coordinate_pattern="src/auth/login.py",
                authority=AuthorityLevel.APPROVE,
                trust_ceiling="proof",
            ),
            Jurisdiction(
                team_id="security",
                coordinate_pattern="src/auth/logout.py",
                authority=AuthorityLevel.APPROVE,
                trust_ceiling="proof",
            ),
            Jurisdiction(
                team_id="platform",
                coordinate_pattern="src/infra/*",
                authority=AuthorityLevel.FULL,
                trust_ceiling="verified",
            ),
        ]
        grants = integrator.to_authority_grants(jurs)
        # security's two patterns should be grouped into one grant
        sec_grants = [g for g in grants if g.receiving_team == "security"]
        assert len(sec_grants) == 1
        assert len(sec_grants[0].coordinate_patterns) == 2

    def test_merge_sources_grant_overrides_codeowners(self):
        integrator = AuthorityIntegrator()
        co_jurs = [
            Jurisdiction(
                team_id="security",
                coordinate_pattern="src/auth/*",
                authority=AuthorityLevel.REVIEW_ONLY,
            )
        ]
        grant_jurs = [
            Jurisdiction(
                team_id="security",
                coordinate_pattern="src/auth/*",
                authority=AuthorityLevel.APPROVE,
                delegation_depth=1,
            )
        ]
        merged = integrator.merge_sources(co_jurs, grant_jurs)
        assert len(merged) == 1
        assert merged[0].authority == AuthorityLevel.APPROVE

    def test_merge_sources_combines_non_overlapping(self):
        integrator = AuthorityIntegrator()
        co_jurs = [
            Jurisdiction(team_id="docs", coordinate_pattern="docs/*")
        ]
        grant_jurs = [
            Jurisdiction(team_id="security", coordinate_pattern="src/auth/*")
        ]
        merged = integrator.merge_sources(co_jurs, grant_jurs)
        team_ids = {j.team_id for j in merged}
        assert "docs" in team_ids
        assert "security" in team_ids
