from __future__ import annotations
"""Surface realization: HLF → surface English text.

Converts Harmonic Logical Forms into grammatical English sentences.
This is the inverse of the semantic parser (LFBuilder): given a structured
logical form, produce a fluent surface string.

Architecture:
  SurfaceRealizer.realize(lf, context) dispatches to sub-methods by LF type.
  EventTerm   → _realize_event_term  (verb-phrase realization)
  Quantifiers → _realize_quantifier  (determiner-phrase construction)
  Connectives → _realize_negation, _realize_conjunction, etc.
  A MorphologyEngine handles all inflection.

Design principles:
  * Frame-centric: every EventTerm maps to a verb via FRAME_TO_VERB.
  * Context-sensitive: pronouns, articles, and definiteness depend on
    discourse state (which referents have been introduced, what is in focus).
  * Grade-aware: when multiple voices are available (PolyTerm), choose the
    highest-graded realization.
  * Compositional: sub-expressions (NPs, PPs, embedded clauses) are realized
    recursively and assembled by linearize().
"""

__all__ = ["SurfaceRealizer", "FRAME_TO_VERB", "RealizationResult"]

from dataclasses import dataclass, field
from typing import Optional, Any
import re

from gofai_chat.core.grade import Grade
from gofai_chat.core.terms import (
    HLF, EventTerm, FrameIntro, NegTerm, ConjTerm, DisjTerm, TenseTerm,
    AspectTerm, ModalTerm, Exists, ForAll, Iota, PolyTerm, CoerceTerm,
    QuoteTerm, ImplTerm, Var, Const, Lam, App,
)
from gofai_chat.core.judgment import Context, GradedJudgment, Referent
from gofai_chat.core.types import Tense, Aspect
from gofai_chat.frames.instances import FrameInstance, RoleFiller
from gofai_chat.generation.morphology import MorphologyEngine, MorphFeatures

# Optional: Grade-aware DM engine for morphological realization
try:
    from gofai_chat.grammar.distributed_morphology import (
        DMEngine,
        ENGLISH_VIS,
        ENGLISH_READJUSTMENTS,
        ENGLISH_IMPOVERISHMENTS,
    )
    from gofai_chat.grammar.features import (
        Feature,
        FeatureBundle,
        PhiFeatures,
        CaseFeature,
        TenseFeature,
        make_verb_features,
        CASE_NOMINATIVE,
        CASE_ACCUSATIVE,
    )
    from gofai_chat.grammar.lfg import FStructure
    _HAS_DM = True
except ImportError:
    _HAS_DM = False


# ---------------------------------------------------------------------------
# Frame-to-verb mapping  (155 frames)
# ---------------------------------------------------------------------------

FRAME_TO_VERB: dict[str, dict[str, Any]] = {
    # ── Motion ──────────────────────────────────────────────────────────────
    "Running":    {"verb": "run",     "agent": "subj", "path": "along",   "goal": "to",   "source": "from"},
    "Walking":    {"verb": "walk",    "agent": "subj", "path": "along",   "goal": "to",   "source": "from"},
    "Flying":     {"verb": "fly",     "agent": "subj", "path": "through", "goal": "to",   "source": "from"},
    "Swimming":   {"verb": "swim",    "agent": "subj", "path": "through", "goal": "to"},
    "Jumping":    {"verb": "jump",    "agent": "subj", "goal": "to",      "source": "from"},
    "Falling":    {"verb": "fall",    "theme": "subj", "goal": "to",      "source": "from"},
    "Coming":     {"verb": "come",    "theme": "subj", "goal": "to",      "source": "from"},
    "Going":      {"verb": "go",      "theme": "subj", "goal": "to",      "source": "from"},
    "Arriving":   {"verb": "arrive",  "theme": "subj", "goal": "at"},
    "Departing":  {"verb": "depart",  "theme": "subj", "source": "from"},
    "Entering":   {"verb": "enter",   "theme": "subj", "goal": "obj"},
    "Leaving":    {"verb": "leave",   "theme": "subj", "source": "obj"},
    "Returning":  {"verb": "return",  "theme": "subj", "goal": "to"},
    "Chasing":    {"verb": "chase",   "agent": "subj", "patient": "obj"},
    "Following":  {"verb": "follow",  "agent": "subj", "leader": "obj"},
    "Leading":    {"verb": "lead",    "leader": "subj","follower": "obj"},
    "Carrying":   {"verb": "carry",   "agent": "subj", "theme": "obj",    "goal": "to"},
    "Bringing":   {"verb": "bring",   "agent": "subj", "theme": "obj",    "goal": "to"},
    "Taking":     {"verb": "take",    "agent": "subj", "theme": "obj",    "source": "from"},
    "Sending":    {"verb": "send",    "agent": "subj", "theme": "obj",    "recipient": "to"},
    "Passing":    {"verb": "pass",    "agent": "subj", "landmark": "by"},
    "Crossing":   {"verb": "cross",   "agent": "subj", "path": "obj"},
    "Climbing":   {"verb": "climb",   "agent": "subj", "goal": "obj"},
    "Descending": {"verb": "descend", "agent": "subj", "source": "from"},
    "Escaping":   {"verb": "escape",  "agent": "subj", "source": "from"},
    "Fleeing":    {"verb": "flee",    "agent": "subj", "source": "from"},
    "Approaching":{"verb": "approach","agent": "subj", "goal": "obj"},
    "Retreating": {"verb": "retreat", "agent": "subj", "source": "from"},
    "Sliding":    {"verb": "slide",   "agent": "subj", "path": "along",   "goal": "to"},
    "Crawling":   {"verb": "crawl",   "agent": "subj", "path": "along",   "goal": "to"},
    "Floating":   {"verb": "float",   "theme": "subj", "medium": "on"},
    "Sinking":    {"verb": "sink",    "theme": "subj", "medium": "in"},
    "Drifting":   {"verb": "drift",   "theme": "subj", "direction": "toward"},
    "Rushing":    {"verb": "rush",    "agent": "subj", "goal": "to"},
    "Wandering":  {"verb": "wander",  "agent": "subj", "path": "through"},
    "Marching":   {"verb": "march",   "agent": "subj", "path": "along",   "goal": "to"},
    "Riding":     {"verb": "ride",    "agent": "subj", "vehicle": "obj",  "goal": "to"},
    "Sailing":    {"verb": "sail",    "agent": "subj", "vehicle": "obj",  "goal": "to"},
    # ── Perception ──────────────────────────────────────────────────────────
    "Seeing":      {"verb": "see",      "perceiver": "subj", "phenomenon": "obj"},
    "Hearing":     {"verb": "hear",     "perceiver": "subj", "phenomenon": "obj"},
    "Smelling":    {"verb": "smell",    "perceiver": "subj", "phenomenon": "obj"},
    "Tasting":     {"verb": "taste",    "perceiver": "subj", "phenomenon": "obj"},
    "Touching":    {"verb": "touch",    "agent": "subj",     "patient": "obj"},
    "Looking_at":  {"verb": "look at",  "perceiver": "subj", "phenomenon": "obj"},
    "Noticing":    {"verb": "notice",   "perceiver": "subj", "phenomenon": "obj"},
    "Watching":    {"verb": "watch",    "perceiver": "subj", "phenomenon": "obj"},
    "Observing":   {"verb": "observe",  "perceiver": "subj", "phenomenon": "obj"},
    "Perceiving":  {"verb": "perceive", "perceiver": "subj", "phenomenon": "obj"},
    "Detecting":   {"verb": "detect",   "perceiver": "subj", "phenomenon": "obj"},
    "Listening":   {"verb": "listen to","perceiver": "subj", "phenomenon": "obj"},
    "Feeling_phys":{"verb": "feel",     "perceiver": "subj", "phenomenon": "obj"},
    "Scanning":    {"verb": "scan",     "agent": "subj",     "patient": "obj"},
    "Inspecting":  {"verb": "inspect",  "agent": "subj",     "patient": "obj"},
    "Examining":   {"verb": "examine",  "agent": "subj",     "patient": "obj"},
    "Gazing":      {"verb": "gaze at",  "perceiver": "subj", "phenomenon": "obj"},
    # ── Cognition ────────────────────────────────────────────────────────────
    "Knowing":       {"verb": "know",        "cognizer": "subj", "topic": "obj"},
    "Thinking":      {"verb": "think",       "cognizer": "subj", "content": "about"},
    "Believing":     {"verb": "believe",     "cognizer": "subj", "content": "obj"},
    "Understanding": {"verb": "understand",  "cognizer": "subj", "topic": "obj"},
    "Remembering":   {"verb": "remember",    "cognizer": "subj", "content": "obj"},
    "Forgetting":    {"verb": "forget",      "cognizer": "subj", "content": "obj"},
    "Learning":      {"verb": "learn",       "student": "subj",  "subject_matter": "obj"},
    "Doubting":      {"verb": "doubt",       "cognizer": "subj", "content": "obj"},
    "Imagining":     {"verb": "imagine",     "cognizer": "subj", "content": "obj"},
    "Expecting":     {"verb": "expect",      "cognizer": "subj", "content": "obj"},
    "Realizing":     {"verb": "realize",     "cognizer": "subj", "content": "obj"},
    "Deciding":      {"verb": "decide",      "cognizer": "subj", "content": "on"},
    "Choosing":      {"verb": "choose",      "agent": "subj",    "theme": "obj"},
    "Planning":      {"verb": "plan",        "agent": "subj",    "content": "obj"},
    "Intending":     {"verb": "intend",      "agent": "subj",    "content": "obj"},
    "Considering":   {"verb": "consider",    "cognizer": "subj", "content": "obj"},
    "Suspecting":    {"verb": "suspect",     "cognizer": "subj", "content": "obj"},
    "Inferring":     {"verb": "infer",       "cognizer": "subj", "content": "obj"},
    "Predicting":    {"verb": "predict",     "cognizer": "subj", "content": "obj"},
    "Hypothesizing": {"verb": "hypothesize", "cognizer": "subj", "content": "obj"},
    "Calculating":   {"verb": "calculate",   "agent": "subj",    "result": "obj"},
    "Analyzing":     {"verb": "analyze",     "agent": "subj",    "content": "obj"},
    "Reasoning":     {"verb": "reason",      "cognizer": "subj", "content": "about"},
    "Concentrating": {"verb": "concentrate", "agent": "subj",    "content": "on"},
    "Contemplating": {"verb": "contemplate", "cognizer": "subj", "content": "obj"},
    "Reflecting":    {"verb": "reflect",     "cognizer": "subj", "content": "on"},
    "Worrying":      {"verb": "worry",       "experiencer": "subj", "content": "about"},
    # ── Communication ────────────────────────────────────────────────────────
    "Saying":        {"verb": "say",         "speaker": "subj", "message": "obj",   "addressee": "to"},
    "Telling":       {"verb": "tell",        "speaker": "subj", "addressee": "obj", "message": "about"},
    "Asking":        {"verb": "ask",         "speaker": "subj", "addressee": "obj", "message": "about"},
    "Answering":     {"verb": "answer",      "speaker": "subj", "addressee": "obj"},
    "Arguing":       {"verb": "argue",       "speaker": "subj", "content": "about"},
    "Explaining":    {"verb": "explain",     "speaker": "subj", "content": "obj",   "addressee": "to"},
    "Describing":    {"verb": "describe",    "speaker": "subj", "content": "obj",   "addressee": "to"},
    "Reporting":     {"verb": "report",      "speaker": "subj", "content": "obj",   "addressee": "to"},
    "Announcing":    {"verb": "announce",    "speaker": "subj", "content": "obj"},
    "Promising":     {"verb": "promise",     "speaker": "subj", "message": "obj",   "addressee": "to"},
    "Warning":       {"verb": "warn",        "speaker": "subj", "addressee": "obj", "content": "about"},
    "Suggesting":    {"verb": "suggest",     "speaker": "subj", "content": "obj",   "addressee": "to"},
    "Recommending":  {"verb": "recommend",   "speaker": "subj", "content": "obj",   "addressee": "to"},
    "Requesting":    {"verb": "request",     "speaker": "subj", "content": "obj",   "addressee": "from"},
    "Ordering":      {"verb": "order",       "speaker": "subj", "addressee": "obj", "content": "obj"},
    "Denying":       {"verb": "deny",        "speaker": "subj", "content": "obj"},
    "Admitting":     {"verb": "admit",       "speaker": "subj", "content": "obj"},
    "Claiming":      {"verb": "claim",       "speaker": "subj", "content": "obj"},
    "Complaining":   {"verb": "complain",    "speaker": "subj", "content": "about", "addressee": "to"},
    "Praising":      {"verb": "praise",      "speaker": "subj", "beneficiary": "obj","content": "for"},
    "Criticizing":   {"verb": "criticize",   "speaker": "subj", "target": "obj",    "content": "for"},
    "Thanking":      {"verb": "thank",       "speaker": "subj", "beneficiary": "obj","content": "for"},
    "Apologizing":   {"verb": "apologize",   "speaker": "subj", "addressee": "to",  "content": "for"},
    "Greeting":      {"verb": "greet",       "agent": "subj",   "recipient": "obj"},
    "Calling":       {"verb": "call",        "caller": "subj",  "recipient": "obj"},
    "Interviewing":  {"verb": "interview",   "interviewer": "subj", "interviewee": "obj"},
    "Debating":      {"verb": "debate",      "agent": "subj",   "co_participant": "with", "topic": "about"},
    "Proposing":     {"verb": "propose",     "agent": "subj",   "content": "obj",   "addressee": "to"},
    "Negotiating":   {"verb": "negotiate",   "agent": "subj",   "co_participant": "with", "content": "obj"},
    "Persuading":    {"verb": "persuade",    "agent": "subj",   "patient": "obj",   "content": "obj"},
    "Convincing":    {"verb": "convince",    "agent": "subj",   "patient": "obj",   "content": "of"},
    "Deceiving":     {"verb": "deceive",     "agent": "subj",   "patient": "obj"},
    "Confessing":    {"verb": "confess",     "speaker": "subj", "content": "obj",   "addressee": "to"},
    "Lecturing":     {"verb": "lecture",     "speaker": "subj", "addressee": "obj", "content": "about"},
    "Broadcasting":  {"verb": "broadcast",   "agent": "subj",   "content": "obj"},
    "Publishing":    {"verb": "publish",     "agent": "subj",   "content": "obj"},
    "Translating":   {"verb": "translate",   "agent": "subj",   "content": "obj",   "goal_language": "into"},
    "Interpreting":  {"verb": "interpret",   "agent": "subj",   "content": "obj"},
    # ── Causation / change-of-state ──────────────────────────────────────────
    "Causation":     {"verb": "cause",       "cause": "subj",   "effect": "obj"},
    "Making":        {"verb": "make",        "agent": "subj",   "product": "obj",   "material": "from"},
    "Creating":      {"verb": "create",      "agent": "subj",   "created_entity": "obj"},
    "Building":      {"verb": "build",       "agent": "subj",   "created_entity": "obj", "material": "from"},
    "Producing":     {"verb": "produce",     "agent": "subj",   "product": "obj"},
    "Destroying":    {"verb": "destroy",     "agent": "subj",   "patient": "obj"},
    "Breaking":      {"verb": "break",       "agent": "subj",   "patient": "obj"},
    "Damaging":      {"verb": "damage",      "agent": "subj",   "patient": "obj"},
    "Repairing":     {"verb": "repair",      "agent": "subj",   "patient": "obj"},
    "Helping":       {"verb": "help",        "agent": "subj",   "beneficiary": "obj","content": "with"},
    "Harming":       {"verb": "harm",        "agent": "subj",   "patient": "obj"},
    "Fixing":        {"verb": "fix",         "agent": "subj",   "patient": "obj"},
    "Improving":     {"verb": "improve",     "agent": "subj",   "patient": "obj"},
    "Changing":      {"verb": "change",      "agent": "subj",   "patient": "obj"},
    "Preventing":    {"verb": "prevent",     "agent": "subj",   "effect": "obj"},
    "Transforming":  {"verb": "transform",   "agent": "subj",   "patient": "obj",   "result": "into"},
    "Converting":    {"verb": "convert",     "agent": "subj",   "patient": "obj",   "result": "into"},
    "Enabling":      {"verb": "enable",      "agent": "subj",   "patient": "obj",   "content": "to"},
    "Forcing":       {"verb": "force",       "agent": "subj",   "patient": "obj",   "content": "obj"},
    "Allowing":      {"verb": "allow",       "agent": "subj",   "patient": "obj",   "content": "obj"},
    "Forbidding":    {"verb": "forbid",      "agent": "subj",   "patient": "obj",   "content": "from"},
    "Motivating":    {"verb": "motivate",    "agent": "subj",   "patient": "obj"},
    "Inspiring":     {"verb": "inspire",     "agent": "subj",   "patient": "obj"},
    "Encouraging":   {"verb": "encourage",   "agent": "subj",   "patient": "obj"},
    "Discouraging":  {"verb": "discourage",  "agent": "subj",   "patient": "obj",   "content": "from"},
    "Replacing":     {"verb": "replace",     "agent": "subj",   "old": "obj",       "new": "with"},
    "Restoring":     {"verb": "restore",     "agent": "subj",   "patient": "obj"},
    # ── Ingestion ───────────────────────────────────────────────────────────
    "Eating":    {"verb": "eat",     "eater": "subj",    "food": "obj"},
    "Drinking":  {"verb": "drink",   "drinker": "subj",  "liquid": "obj"},
    "Ingesting": {"verb": "ingest",  "agent": "subj",    "substance": "obj"},
    "Swallowing":{"verb": "swallow", "agent": "subj",    "substance": "obj"},
    "Cooking":   {"verb": "cook",    "agent": "subj",    "food": "obj"},
    "Baking":    {"verb": "bake",    "agent": "subj",    "food": "obj"},
    "Frying":    {"verb": "fry",     "agent": "subj",    "food": "obj"},
    "Serving":   {"verb": "serve",   "agent": "subj",    "food": "obj",    "recipient": "to"},
    "Feeding":   {"verb": "feed",    "agent": "subj",    "recipient": "obj","food": "obj"},
    # ── Emotion / affect ────────────────────────────────────────────────────
    "Loving":    {"verb": "love",    "experiencer": "subj", "stimulus": "obj"},
    "Hating":    {"verb": "hate",    "experiencer": "subj", "stimulus": "obj"},
    "Liking":    {"verb": "like",    "experiencer": "subj", "stimulus": "obj"},
    "Fearing":   {"verb": "fear",    "experiencer": "subj", "stimulus": "obj"},
    "Enjoying":  {"verb": "enjoy",   "experiencer": "subj", "stimulus": "obj"},
    "Wanting":   {"verb": "want",    "experiencer": "subj", "stimulus": "obj"},
    "Needing":   {"verb": "need",    "experiencer": "subj", "stimulus": "obj"},
    "Hoping":    {"verb": "hope",    "experiencer": "subj", "content": "for"},
    "Suffering": {"verb": "suffer",  "experiencer": "subj", "cause": "from"},
    "Rejoicing": {"verb": "rejoice", "experiencer": "subj", "content": "at"},
    "Regretting":{"verb": "regret",  "experiencer": "subj", "content": "obj"},
    "Missing":   {"verb": "miss",    "experiencer": "subj", "stimulus": "obj"},
    "Admiring":  {"verb": "admire",  "experiencer": "subj", "stimulus": "obj"},
    "Trusting":  {"verb": "trust",   "experiencer": "subj", "stimulus": "obj"},
    "Envying":   {"verb": "envy",    "experiencer": "subj", "stimulus": "obj"},
    "Mourning":  {"verb": "mourn",   "experiencer": "subj", "theme": "obj"},
    "Craving":   {"verb": "crave",   "experiencer": "subj", "stimulus": "obj"},
    "Resenting": {"verb": "resent",  "experiencer": "subj", "stimulus": "obj"},
    "Despising": {"verb": "despise", "experiencer": "subj", "stimulus": "obj"},
    "Cherishing":{"verb": "cherish", "experiencer": "subj", "stimulus": "obj"},
    "Pitying":   {"verb": "pity",    "experiencer": "subj", "stimulus": "obj"},
    # ── Social / interaction ─────────────────────────────────────────────────
    "Meeting":     {"verb": "meet",    "agent": "subj",    "co_participant": "obj"},
    "Visiting":    {"verb": "visit",   "agent": "subj",    "goal": "obj"},
    "Attacking":   {"verb": "attack",  "attacker": "subj", "victim": "obj"},
    "Defending":   {"verb": "defend",  "defender": "subj", "patient": "obj",  "cause": "from"},
    "Competing":   {"verb": "compete", "agent": "subj",    "co_participant": "with"},
    "Cooperating": {"verb": "cooperate","agent": "subj",   "co_participant": "with"},
    "Marrying":    {"verb": "marry",   "agent": "subj",    "co_participant": "obj"},
    "Hiring":      {"verb": "hire",    "employer": "subj", "employee": "obj"},
    "Firing":      {"verb": "fire",    "employer": "subj", "employee": "obj"},
    "Buying":      {"verb": "buy",     "buyer": "subj",    "goods": "obj",    "seller": "from"},
    "Selling":     {"verb": "sell",    "seller": "subj",   "goods": "obj",    "buyer": "to"},
    "Giving":      {"verb": "give",    "donor": "subj",    "theme": "obj",    "recipient": "to"},
    "Receiving":   {"verb": "receive", "recipient": "subj","theme": "obj",    "source": "from"},
    "Lending":     {"verb": "lend",    "lender": "subj",   "theme": "obj",    "borrower": "to"},
    "Borrowing":   {"verb": "borrow",  "borrower": "subj", "theme": "obj",    "source": "from"},
    "Paying":      {"verb": "pay",     "payer": "subj",    "money": "obj",    "recipient": "to"},
    "Stealing":    {"verb": "steal",   "agent": "subj",    "theme": "obj",    "victim": "from"},
    "Sharing":     {"verb": "share",   "agent": "subj",    "theme": "obj",    "co_participant": "with"},
    "Donating":    {"verb": "donate",  "donor": "subj",    "theme": "obj",    "recipient": "to"},
    "Inviting":    {"verb": "invite",  "agent": "subj",    "guest": "obj",    "event": "to"},
    "Attending":   {"verb": "attend",  "agent": "subj",    "event": "obj"},
    "Winning":     {"verb": "win",     "winner": "subj",   "prize": "obj"},
    "Protecting":  {"verb": "protect", "protector": "subj","patient": "obj",  "cause": "from"},
    "Saving":      {"verb": "save",    "agent": "subj",    "patient": "obj",  "cause": "from"},
    "Rescuing":    {"verb": "rescue",  "agent": "subj",    "patient": "obj",  "cause": "from"},
    "Accepting":   {"verb": "accept",  "agent": "subj",    "content": "obj"},
    "Refusing":    {"verb": "refuse",  "agent": "subj",    "content": "obj"},
    "Agreeing":    {"verb": "agree",   "agent": "subj",    "co_participant": "with", "content": "on"},
    "Disagreeing": {"verb": "disagree","agent": "subj",    "co_participant": "with"},
    "Voting":      {"verb": "vote",    "agent": "subj",    "candidate": "for"},
    "Electing":    {"verb": "elect",   "agent": "subj",    "elected": "obj"},
    "Appointing":  {"verb": "appoint", "agent": "subj",    "appointed": "obj","role": "as"},
    "Blaming":     {"verb": "blame",   "agent": "subj",    "patient": "obj",  "content": "for"},
    "Forgiving":   {"verb": "forgive", "forgiver": "subj", "patient": "obj",  "content": "for"},
    "Punishing":   {"verb": "punish",  "agent": "subj",    "patient": "obj",  "content": "for"},
    "Rewarding":   {"verb": "reward",  "agent": "subj",    "patient": "obj",  "content": "for"},
    "Accusing":    {"verb": "accuse",  "accuser": "subj",  "accused": "obj",  "content": "of"},
    "Praying":     {"verb": "pray",    "agent": "subj",    "addressee": "to", "content": "for"},
    # ── State / attribute ────────────────────────────────────────────────────
    "Being_Located":      {"verb": "be",       "theme": "subj",  "location": "at",  "copular": True},
    "Possession":         {"verb": "have",     "owner": "subj",  "possession": "obj","copular": True},
    "Existence":          {"verb": "exist",    "entity": "subj"},
    "Similarity":         {"verb": "resemble", "entity": "subj", "co_participant": "obj"},
    "Identity":           {"verb": "be",       "entity1": "subj","entity2": "obj",  "copular": True},
    "Attribute":          {"verb": "be",       "entity": "subj", "attribute": "obj","copular": True},
    "Property_ascription":{"verb": "be",       "entity": "subj", "property": "obj", "copular": True},
    "Membership":         {"verb": "belong to","member": "subj", "group": "obj"},
    "Containment":        {"verb": "contain",  "container": "subj","contents": "obj"},
    "Inclusion":          {"verb": "include",  "whole": "subj",  "part": "obj"},
    "Becoming":           {"verb": "become",   "entity": "subj", "attribute": "obj"},
    "Remaining":          {"verb": "remain",   "theme": "subj",  "attribute": "obj"},
    "Measuring":          {"verb": "measure",  "agent": "subj",  "patient": "obj"},
    "Appearing":          {"verb": "appear",   "theme": "subj",  "attribute": "as"},
    "Seeming":            {"verb": "seem",     "theme": "subj",  "attribute": "obj"},
    # ── Life events ──────────────────────────────────────────────────────────
    "Killing":    {"verb": "kill",    "killer": "subj",      "victim": "obj"},
    "Dying":      {"verb": "die",     "protagonist": "subj", "cause": "from"},
    "Being_born": {"verb": "be born", "protagonist": "subj", "place": "in"},
    "Surviving":  {"verb": "survive", "protagonist": "subj", "situation": "obj"},
    "Beginning":  {"verb": "begin",   "agent": "subj",       "event": "obj"},
    "Ending":     {"verb": "end",     "agent": "subj",       "event": "obj"},
    "Stopping":   {"verb": "stop",    "agent": "subj",       "event": "obj"},
    "Continuing": {"verb": "continue","agent": "subj",       "event": "obj"},
    # ── Work / creation ──────────────────────────────────────────────────────
    "Working":       {"verb": "work",      "agent": "subj",   "activity": "on"},
    "Studying":      {"verb": "study",     "student": "subj", "subject": "obj"},
    "Teaching":      {"verb": "teach",     "teacher": "subj", "student": "obj",  "subject": "obj"},
    "Reading":       {"verb": "read",      "reader": "subj",  "text": "obj"},
    "Writing":       {"verb": "write",     "writer": "subj",  "text": "obj"},
    "Painting":      {"verb": "paint",     "agent": "subj",   "depiction": "obj"},
    "Drawing":       {"verb": "draw",      "agent": "subj",   "depiction": "obj"},
    "Singing":       {"verb": "sing",      "singer": "subj",  "song": "obj"},
    "Playing_music": {"verb": "play",      "agent": "subj",   "instrument": "obj"},
    "Playing_game":  {"verb": "play",      "agent": "subj",   "game": "obj"},
    "Designing":     {"verb": "design",    "agent": "subj",   "created_entity": "obj"},
    "Inventing":     {"verb": "invent",    "agent": "subj",   "created_entity": "obj"},
    "Developing":    {"verb": "develop",   "agent": "subj",   "theme": "obj"},
    "Implementing":  {"verb": "implement", "agent": "subj",   "plan": "obj"},
    "Managing":      {"verb": "manage",    "agent": "subj",   "patient": "obj"},
    "Controlling":   {"verb": "control",   "agent": "subj",   "patient": "obj"},
    "Organizing":    {"verb": "organize",  "agent": "subj",   "patient": "obj"},
    "Practicing":    {"verb": "practice",  "agent": "subj",   "activity": "obj"},
    "Training":      {"verb": "train",     "trainer": "subj", "trainee": "obj"},
    # ── Physical manipulation ────────────────────────────────────────────────
    "Hitting":    {"verb": "hit",    "agent": "subj",  "patient": "obj",  "instrument": "with"},
    "Pulling":    {"verb": "pull",   "agent": "subj",  "theme": "obj"},
    "Pushing":    {"verb": "push",   "agent": "subj",  "theme": "obj"},
    "Lifting":    {"verb": "lift",   "agent": "subj",  "theme": "obj"},
    "Throwing":   {"verb": "throw",  "agent": "subj",  "theme": "obj",   "goal": "to"},
    "Catching":   {"verb": "catch",  "agent": "subj",  "theme": "obj"},
    "Dropping":   {"verb": "drop",   "agent": "subj",  "theme": "obj"},
    "Opening":    {"verb": "open",   "agent": "subj",  "patient": "obj"},
    "Closing":    {"verb": "close",  "agent": "subj",  "patient": "obj"},
    "Cutting":    {"verb": "cut",    "agent": "subj",  "patient": "obj",  "instrument": "with"},
    "Holding":    {"verb": "hold",   "agent": "subj",  "theme": "obj"},
    "Inserting":  {"verb": "insert", "agent": "subj",  "theme": "obj",   "goal": "into"},
    "Removing":   {"verb": "remove", "agent": "subj",  "theme": "obj",   "source": "from"},
    "Covering":   {"verb": "cover",  "agent": "subj",  "patient": "obj", "theme": "with"},
    "Filling":    {"verb": "fill",   "agent": "subj",  "patient": "obj", "theme": "with"},
    "Cleaning":   {"verb": "clean",  "agent": "subj",  "patient": "obj"},
    "Washing":    {"verb": "wash",   "agent": "subj",  "patient": "obj"},
    "Moving_obj": {"verb": "move",   "agent": "subj",  "theme": "obj",   "goal": "to"},
    "Placing":    {"verb": "place",  "agent": "subj",  "theme": "obj",   "goal": "on"},
    "Using":      {"verb": "use",    "agent": "subj",  "instrument": "obj","purpose": "for"},
    "Showing":    {"verb": "show",   "agent": "subj",  "theme": "obj",   "recipient": "to"},
    "Loading":    {"verb": "load",   "agent": "subj",  "theme": "obj",   "goal": "onto"},
    "Unloading":  {"verb": "unload", "agent": "subj",  "theme": "obj",   "source": "from"},
    "Mixing":     {"verb": "mix",    "agent": "subj",  "theme": "obj",   "co_participant": "with"},
    "Heating":    {"verb": "heat",   "agent": "subj",  "patient": "obj"},
    "Cooling":    {"verb": "cool",   "agent": "subj",  "patient": "obj"},
    "Gathering":  {"verb": "gather", "agent": "subj",  "theme": "obj"},
    "Shooting":   {"verb": "shoot",  "agent": "subj",  "patient": "obj", "instrument": "with"},
    # ── Sleep / rest / gesture ───────────────────────────────────────────────
    "Sleeping":  {"verb": "sleep",   "sleeper": "subj"},
    "Waking":    {"verb": "wake up", "agent": "subj"},
    "Resting":   {"verb": "rest",    "agent": "subj"},
    "Waiting":   {"verb": "wait",    "agent": "subj",   "content": "for"},
    "Breathing": {"verb": "breathe", "agent": "subj"},
    "Smiling":   {"verb": "smile",   "agent": "subj",   "recipient": "at"},
    "Laughing":  {"verb": "laugh",   "agent": "subj",   "cause": "at"},
    "Crying":    {"verb": "cry",     "agent": "subj",   "cause": "about"},
    "Shouting":  {"verb": "shout",   "agent": "subj",   "addressee": "at", "message": "obj"},
    "Nodding":   {"verb": "nod",     "agent": "subj"},
    "Pointing":  {"verb": "point",   "agent": "subj",   "direction": "at"},
    # ── Searching / finding ──────────────────────────────────────────────────
    "Finding":     {"verb": "find",       "agent": "subj", "theme": "obj"},
    "Searching":   {"verb": "search for", "agent": "subj", "theme": "obj"},
    "Obtaining":   {"verb": "obtain",     "agent": "subj", "theme": "obj"},
    "Losing":      {"verb": "lose",       "agent": "subj", "theme": "obj"},
    "Discovering": {"verb": "discover",   "agent": "subj", "theme": "obj"},
    "Hiding":      {"verb": "hide",       "agent": "subj", "theme": "obj"},
    "Revealing":   {"verb": "reveal",     "agent": "subj", "theme": "obj",    "recipient": "to"},
    "Seeking":     {"verb": "seek",       "agent": "subj", "theme": "obj"},
    "Retrieving":  {"verb": "retrieve",   "agent": "subj", "theme": "obj",    "source": "from"},
    "Storing":     {"verb": "store",      "agent": "subj", "theme": "obj",    "goal": "in"},
    # ── Research / evaluation ────────────────────────────────────────────────
    "Trying":        {"verb": "try",          "agent": "subj", "activity": "obj"},
    "Succeeding":    {"verb": "succeed",      "agent": "subj", "activity": "in"},
    "Failing":       {"verb": "fail",         "agent": "subj", "activity": "at"},
    "Preparing":     {"verb": "prepare",      "agent": "subj", "patient": "obj"},
    "Evaluating":    {"verb": "evaluate",     "agent": "subj", "content": "obj"},
    "Judging":       {"verb": "judge",        "agent": "subj", "content": "obj"},
    "Testing":       {"verb": "test",         "agent": "subj", "patient": "obj"},
    "Recording":     {"verb": "record",       "agent": "subj", "content": "obj"},
    "Photographing": {"verb": "photograph",   "agent": "subj", "patient": "obj"},
    "Estimating":    {"verb": "estimate",     "agent": "subj", "value": "obj"},
    "Verifying":     {"verb": "verify",       "agent": "subj", "content": "obj"},
    "Proving":       {"verb": "prove",        "agent": "subj", "content": "obj"},
    "Refuting":      {"verb": "refute",       "agent": "subj", "content": "obj"},
    "Supporting":    {"verb": "support",      "agent": "subj", "patient": "obj"},
    "Opposing":      {"verb": "oppose",       "agent": "subj", "patient": "obj"},
    "Celebrating":   {"verb": "celebrate",    "agent": "subj", "event": "obj"},
    "Comparing":     {"verb": "compare",      "agent": "subj", "entity1": "obj",  "entity2": "with"},
    "Combining":     {"verb": "combine",      "agent": "subj", "theme": "obj",    "co_participant": "with"},
    "Separating":    {"verb": "separate",     "agent": "subj", "theme": "obj",    "co_participant": "from"},
    "Connecting":    {"verb": "connect",      "agent": "subj", "theme": "obj",    "co_participant": "to"},
    "Performing":    {"verb": "perform",      "agent": "subj", "role": "obj",     "audience": "for"},
    "Representing":  {"verb": "represent",    "agent": "subj", "content": "obj"},
    "Expressing":    {"verb": "express",      "agent": "subj", "content": "obj"},
    "Naming":        {"verb": "name",         "agent": "subj", "patient": "obj",  "attribute": "obj"},
    "Counting":      {"verb": "count",        "agent": "subj", "theme": "obj"},
    "Signing":       {"verb": "sign",         "agent": "subj", "document": "obj"},
    "Guarding":      {"verb": "guard",        "agent": "subj", "patient": "obj"},
    "Challenging":   {"verb": "challenge",    "agent": "subj", "patient": "obj"},
    "Questioning":   {"verb": "question",     "agent": "subj", "content": "obj"},
    "Burning":       {"verb": "burn",         "theme": "subj"},
    "Melting":       {"verb": "melt",         "theme": "subj"},
    "Freezing":      {"verb": "freeze",       "theme": "subj"},
    "Shrinking":     {"verb": "shrink",       "theme": "subj"},
    "Offering":      {"verb": "offer",        "agent": "subj", "recipient": "obj","theme": "obj"},
    "Declining":     {"verb": "decline",      "agent": "subj", "offer": "obj"},
    "Participating": {"verb": "participate in","agent": "subj","event": "obj"},
    "Distributing":  {"verb": "distribute",   "agent": "subj", "theme": "obj",    "recipient": "to"},
    "Collecting":    {"verb": "collect",      "agent": "subj", "theme": "obj"},
    "Spreading":     {"verb": "spread",       "agent": "subj", "theme": "obj",    "goal": "over"},
    "Reducing":      {"verb": "reduce",       "agent": "subj", "theme": "obj"},
    "Increasing":    {"verb": "increase",     "agent": "subj", "theme": "obj"},
    "Adding":        {"verb": "add",          "agent": "subj", "theme": "obj",    "goal": "to"},
    "Hunting":       {"verb": "hunt",         "agent": "subj", "prey": "obj"},
    "Harvesting":    {"verb": "harvest",      "agent": "subj", "crop": "obj"},
    "Investing":     {"verb": "invest",       "investor": "subj","amount": "obj", "goal": "in"},
}

# Merge with the FrameVerbDB (FrameNet + VerbNet + ConceptNet).
# Curated entries above take priority; FrameNet/VerbNet fill the gaps.
try:
    from gofai_chat.data.frame_verb_db import FRAME_VERB_DB as _fvdb
    _merged = dict(_fvdb)           # FrameNet + VerbNet + curated overrides
    _merged.update(FRAME_TO_VERB)   # hand-tuned entries win
    FRAME_TO_VERB = _merged
except Exception:
    pass  # fall back to the curated-only dict above


# ---------------------------------------------------------------------------
# Realization result
# ---------------------------------------------------------------------------

@dataclass
class RealizationResult:
    """Output of one realization step, carrying text and metadata.

    Attributes:
        text:              Surface string.
        grade:             Quality/confidence score.
        features:          Syntactic features for downstream agreement.
        is_pronominalized: Whether the head NP was a pronoun.
        is_definitized:    Whether the head NP used "the".
        head_noun:         The head noun if available.
    """

    text: str
    grade: Grade = field(default_factory=Grade.perfect)
    features: dict[str, Any] = field(default_factory=dict)
    is_pronominalized: bool = False
    is_definitized: bool = False
    head_noun: str = ""

    def __str__(self) -> str:
        return self.text


# ---------------------------------------------------------------------------
# Module-level lookup tables
# ---------------------------------------------------------------------------

_PRONOUN_TABLE: dict[tuple[int, str, str, str], str] = {
    (1, "singular", "common",    "nominative"): "I",
    (1, "singular", "common",    "accusative"):  "me",
    (1, "singular", "common",    "genitive"):    "my",
    (1, "plural",   "common",    "nominative"):  "we",
    (1, "plural",   "common",    "accusative"):  "us",
    (1, "plural",   "common",    "genitive"):    "our",
    (2, "singular", "common",    "nominative"):  "you",
    (2, "singular", "common",    "accusative"):  "you",
    (2, "singular", "common",    "genitive"):    "your",
    (2, "plural",   "common",    "nominative"):  "you",
    (2, "plural",   "common",    "accusative"):  "you",
    (2, "plural",   "common",    "genitive"):    "your",
    (3, "singular", "masculine", "nominative"):  "he",
    (3, "singular", "masculine", "accusative"):  "him",
    (3, "singular", "masculine", "genitive"):    "his",
    (3, "singular", "feminine",  "nominative"):  "she",
    (3, "singular", "feminine",  "accusative"):  "her",
    (3, "singular", "feminine",  "genitive"):    "her",
    (3, "singular", "neuter",    "nominative"):  "it",
    (3, "singular", "neuter",    "accusative"):  "it",
    (3, "singular", "neuter",    "genitive"):    "its",
    (3, "singular", "common",    "nominative"):  "they",
    (3, "singular", "common",    "accusative"):  "them",
    (3, "singular", "common",    "genitive"):    "their",
    (3, "plural",   "common",    "nominative"):  "they",
    (3, "plural",   "common",    "accusative"):  "them",
    (3, "plural",   "common",    "genitive"):    "their",
    (3, "plural",   "masculine", "nominative"):  "they",
    (3, "plural",   "masculine", "accusative"):  "them",
    (3, "plural",   "masculine", "genitive"):    "their",
    (3, "plural",   "feminine",  "nominative"):  "they",
    (3, "plural",   "feminine",  "accusative"):  "them",
    (3, "plural",   "feminine",  "genitive"):    "their",
}

_MODAL_WORDS: dict[str, str] = {
    "epistemic_possible":  "might",
    "epistemic_probable":  "should",
    "epistemic_necessary": "must",
    "deontic_possible":    "may",
    "deontic_permitted":   "may",
    "deontic_obligatory":  "must",
    "deontic_necessary":   "must",
    "dynamic_possible":    "can",
    "dynamic_able":        "can",
    "dynamic_willing":     "will",
    "conditional":         "would",
    "counterfactual":      "would",
    "epistemic":           "might",
    "deontic":             "should",
    "dynamic":             "can",
    "possible":            "might",
    "necessary":           "must",
    "permitted":           "may",
    "able":                "can",
    "willing":             "will",
    "habitual":            "would",
    "ought":               "ought to",
}

_TEMPORAL_PREPS: frozenset[str] = frozenset(
    {"before", "after", "during", "while", "when", "since",
     "until", "at", "on", "in", "by", "from"}
)

_SUBJECT_ROLES: tuple[str, ...] = (
    "agent", "experiencer", "perceiver", "speaker", "cognizer",
    "subject", "killer", "attacker", "defender", "buyer", "seller",
    "payer", "donor", "lender", "borrower", "teacher", "trainer",
    "employer", "theme", "entity", "entity1", "protagonist",
)

_OBJECT_ROLES: tuple[str, ...] = (
    "patient", "theme", "food", "liquid", "message", "content",
    "phenomenon", "product", "created_entity", "topic", "subject_matter",
    "goods", "instrument", "text", "result", "prize", "possession", "entity2",
)

_MASS_NOUNS: frozenset[str] = frozenset({
    "water", "air", "fire", "earth", "sand", "gold", "silver", "iron",
    "wood", "glass", "paper", "rice", "wheat", "flour", "milk", "blood",
    "oil", "gas", "music", "art", "love", "peace", "knowledge", "advice",
    "information", "progress", "news", "evidence", "research", "education",
    "beauty", "happiness", "sadness", "anger", "fear", "hope", "trust",
    "food", "money", "time", "space", "light", "darkness", "heat", "cold",
    "traffic", "furniture", "luggage", "equipment", "software", "hardware",
    "clothing", "grass", "snow", "rain", "sunshine", "pollution", "chaos",
    "harmony", "silence", "noise", "dust", "smoke", "steam", "electricity",
    "gravity", "justice", "freedom", "wisdom", "courage", "patience",
    "sympathy", "empathy", "compassion", "mercy", "grace", "glory",
})

# Verb-to-passive-participle for passive voice realization
_COPULAR_LOCATION_PREPS: dict[str, str] = {
    "on": "on",
    "in": "in",
    "at": "at",
    "near": "near",
    "beside": "beside",
    "behind": "behind",
    "above": "above",
    "below": "below",
    "under": "under",
    "over": "over",
    "between": "between",
    "among": "among",
    "inside": "inside",
    "outside": "outside",
    "within": "within",
    "against": "against",
    "across": "across",
    "along": "along",
    "through": "through",
    "around": "around",
}

# Prepositions by which PP adjunct should be realized in "location" roles
_LOCATION_TO_PREP: dict[str, str] = {
    "location": "at",
    "place": "at",
    "site": "at",
    "venue": "at",
    "locale": "in",
    "region": "in",
    "area": "in",
    "room": "in",
    "building": "in",
    "city": "in",
    "country": "in",
    "land": "in",
}

# Role names that are typically NOT verbalized (internal/structural)
_INTERNAL_ROLES: frozenset[str] = frozenset({
    "frame_type", "event_type", "event_var", "grade", "source_span",
})


# ---------------------------------------------------------------------------
# SurfaceRealizer
# ---------------------------------------------------------------------------

class SurfaceRealizer:
    """Converts HLF terms into fluent English surface strings.

    The realizer is context-sensitive: it tracks which referents have
    been introduced into the discourse model and chooses pronouns or
    definite NPs accordingly.

    Key methods:
      * :meth:`realize` — recursive dispatch; does not capitalize/punctuate.
      * :meth:`realize_sentence` — full sentence (capitalizes, adds period).
      * :meth:`realize_judgment` — realizes the term inside a GradedJudgment.
      * :meth:`realize_list` — realizes a list of LFs as multiple sentences.
      * :meth:`reset` — clears per-utterance state.

    Usage::

        ctx = Context()
        r = SurfaceRealizer()
        text = r.realize_sentence(my_lf, ctx)
    """

    def __init__(self) -> None:
        """Initialize the surface realizer with MorphologyEngine and DMEngine."""
        self._morph: MorphologyEngine = MorphologyEngine()
        # Grade-aware DM engine (new)
        if _HAS_DM:
            self._dm = DMEngine(ENGLISH_VIS, ENGLISH_READJUSTMENTS, ENGLISH_IMPOVERISHMENTS)
        else:
            self._dm = None
        # Per-utterance state
        self._introduced: set[str] = set()
        self._current_tense: str = "present"
        self._current_aspect: str = "simple"
        self._current_modal: Optional[str] = None
        self._current_voice: str = "active"
        self._default_person: int = 3
        self._default_number: str = "singular"

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def realize_from_fstructure(self, fs: "FStructure", context: Context) -> str:
        """Realize an LFG FStructure as a surface English string.

        Converts the FStructure to an EventTerm and delegates to the
        standard realization pipeline, using Grade-aware DMEngine
        for morphological exponence.

        Args:
            fs: An LFG FStructure (from gofai_chat.grammar.lfg).
            context: Discourse context.

        Returns:
            A surface English sentence string.
        """
        if not _HAS_DM:
            # Fallback: convert to EventTerm and realize normally
            et = fs.to_event_term()
            return self.realize_sentence(et, context)

        et = fs.to_event_term()
        return self.realize_sentence(et, context)

    def realize(self, lf: HLF, context: Context) -> str:
        """Recursively realize *lf* as a surface English string.

        Does NOT capitalize or add terminal punctuation; call
        :meth:`realize_sentence` for a complete sentence.

        Args:
            lf:      The logical form to realize.
            context: Discourse context for reference resolution,
                     register, and pronominalization.

        Returns:
            A non-empty English string (may be a clause, phrase, or word).
        """
        # Tense wrapper ─────────────────────────────────────────────────────
        if isinstance(lf, TenseTerm):
            saved = self._current_tense
            self._current_tense = lf.tense
            result = self.realize(lf.body, context)
            self._current_tense = saved
            return result

        # Aspect wrapper ────────────────────────────────────────────────────
        if isinstance(lf, AspectTerm):
            saved = self._current_aspect
            self._current_aspect = lf.aspect
            result = self.realize(lf.body, context)
            self._current_aspect = saved
            return result

        # Modal wrapper ─────────────────────────────────────────────────────
        if isinstance(lf, ModalTerm):
            # Realize body with reset tense to ensure base-form VP
            saved_t = self._current_tense
            self._current_tense = "infinitive"
            inner_text = self.realize(lf.body, context)
            self._current_tense = saved_t
            return self._realize_modal(lf.modal_kind, inner_text)

        # Event / frame intro ───────────────────────────────────────────────
        if isinstance(lf, (EventTerm, FrameIntro)):
            return self._realize_event_term(lf, context)

        # Logical connectives ───────────────────────────────────────────────
        if isinstance(lf, NegTerm):
            return self._realize_negation(lf, context)

        if isinstance(lf, ConjTerm):
            return self._realize_conjunction(lf, context)

        if isinstance(lf, DisjTerm):
            return self._realize_disjunction(lf, context)

        if isinstance(lf, ImplTerm):
            ante = self.realize(lf.antecedent, context)
            cons = self.realize(lf.consequent, context)
            return f"if {ante}, then {cons}"

        # Quantifiers ───────────────────────────────────────────────────────
        if isinstance(lf, (Exists, ForAll, Iota)):
            return self._realize_quantifier(lf, context)

        # Polyphony: select best voice ──────────────────────────────────────
        if isinstance(lf, PolyTerm):
            return self._realize_poly(lf, context)

        # Quotation ─────────────────────────────────────────────────────────
        if isinstance(lf, QuoteTerm):
            inner = self.realize(lf.body, context)
            return f'"{inner}"'

        # Coercion: pass through to underlying term ─────────────────────────
        if isinstance(lf, CoerceTerm):
            return self.realize(lf.body, context)

        # Lambda: realize body (generation rarely involves lambdas) ─────────
        if isinstance(lf, Lam):
            return self.realize(lf.body, context)

        # Application ───────────────────────────────────────────────────────
        if isinstance(lf, App):
            return self.realize(lf.func, context)

        # Variables ─────────────────────────────────────────────────────────
        if isinstance(lf, Var):
            ref = context.find_referent(lf.name)
            if ref is not None:
                return self._np_from_referent(ref, context, "nominative")
            return lf.name.replace("_", " ")

        # Constants ─────────────────────────────────────────────────────────
        if isinstance(lf, Const):
            return self._realize_const(lf, context)

        # Unknown term: best-effort fallback
        return str(lf).replace("_", " ")

    def realize_judgment(self, j: GradedJudgment) -> str:
        """Realize the term inside a graded typing judgment as a sentence.

        Args:
            j: GradedJudgment with .term, .context, and .grade.

        Returns:
            A capitalized, punctuated English sentence.
        """
        self.reset()
        raw = self.realize(j.term, j.context)
        return self._capitalize_and_punctuate(raw)

    def realize_sentence(self, lf: HLF, context: Context) -> str:
        """Realize *lf* as a complete, stand-alone sentence.

        Resets per-utterance state, realizes, capitalizes, and adds
        terminal punctuation.

        Args:
            lf:      Logical form to realize.
            context: Discourse context.

        Returns:
            A capitalized, punctuated English sentence.
        """
        self.reset()
        raw = self.realize(lf, context)
        return self._capitalize_and_punctuate(raw)

    def realize_list(
        self,
        lfs: list[HLF],
        context: Context,
        separator: str = " ",
    ) -> str:
        """Realize a list of LFs as separate sentences.

        State is reset between sentences so that reference-tracking
        behaves correctly across sentence boundaries.

        Args:
            lfs:       Logical forms to realize.
            context:   Shared discourse context.
            separator: String joining sentences.

        Returns:
            Multi-sentence string.
        """
        sentences: list[str] = []
        for lf in lfs:
            self.reset()
            sent = self.realize_sentence(lf, context)
            sentences.append(sent)
        return separator.join(sentences)

    def reset(self) -> None:
        """Clear per-utterance state (tense, introduced referents, etc.)."""
        self._introduced = set()
        self._current_tense = "present"
        self._current_aspect = "simple"
        self._current_modal = None
        self._current_voice = "active"

    def set_perspective(self, person: int, number: str = "singular") -> None:
        """Set the default person/number perspective for generation.

        Affects subject-verb agreement and default pronoun form.

        Args:
            person: 1 (I/we), 2 (you), or 3 (he/she/it/they).
            number: "singular" or "plural".
        """
        self._default_person = person
        self._default_number = number

    # -----------------------------------------------------------------------
    # Event term realization
    # -----------------------------------------------------------------------

    def _realize_event_term(
        self,
        et: "EventTerm | FrameIntro",
        context: Context,
    ) -> str:
        """Realize an EventTerm or FrameIntro as a verbal clause.

        Looks up FRAME_TO_VERB for the syntactic template, then:
        1. Identifies and realizes the subject NP.
        2. Inflects the main verb for tense, aspect, and agreement.
        3. Identifies and realizes the direct object NP.
        4. Builds all PP adjuncts for remaining roles.
        5. Assembles everything via linearize().

        Args:
            et:      Event or frame-introduction term.
            context: Discourse context.

        Returns:
            A realized verbal clause (no terminal punctuation).
        """
        frame_name = et.frame_type_name
        roles: dict[str, HLF] = (
            et.roles if isinstance(et, EventTerm) else et.role_fillers
        )

        spec = FRAME_TO_VERB.get(frame_name)
        if spec is None:
            return self._realize_unknown_frame(frame_name, roles, context)

        verb_lemma: str = spec["verb"]
        is_copular: bool = bool(spec.get("copular", False))

        # Subject ────────────────────────────────────────────────────────────
        subj_role = self._find_subject_role(spec, roles)
        subj_val = roles.get(subj_role)
        subj_str = (
            self._realize_np(subj_val, context, role_name=subj_role)
            if subj_val is not None
            else "someone"
        )

        # Morph features for verb agreement ────────────────────────────────
        morph_feats = self._np_morph_features(subj_val, context)

        # Inflect verb ──────────────────────────────────────────────────────
        verb_parts = verb_lemma.split()
        base_verb = verb_parts[0]
        verb_suffix = " ".join(verb_parts[1:]) if len(verb_parts) > 1 else ""
        inflected = self._morph.conjugate(
            base_verb,
            MorphFeatures(
                tense=self._current_tense,
                aspect=self._current_aspect,
                person=morph_feats.person,
                number=morph_feats.number,
            ),
        )
        verb_str = (inflected + (" " + verb_suffix if verb_suffix else "")).strip()

        # Direct object ─────────────────────────────────────────────────────
        obj_role = self._find_object_role(spec, roles, subj_role)
        obj_str = ""
        if obj_role and obj_role in roles:
            obj_str = self._realize_np(roles[obj_role], context, role_name=obj_role)

        # PP adjuncts ───────────────────────────────────────────────────────
        pp_strings = self._build_pp_adjuncts(spec, roles, context, subj_role, obj_role)

        # Assemble ──────────────────────────────────────────────────────────
        parts = [p for p in [subj_str, verb_str, obj_str] if p]
        parts.extend(pp_strings)
        return self.linearize({"parts": parts})

    def _realize_unknown_frame(
        self,
        frame_name: str,
        roles: dict[str, HLF],
        context: Context,
    ) -> str:
        """Best-effort realization for frames absent from FRAME_TO_VERB.

        Derives a verb from the frame name (CamelCase → spaced words)
        and appends role fillers as parenthetical annotations.

        Args:
            frame_name: The unknown frame name.
            roles:      Role-filler dict.
            context:    Discourse context.

        Returns:
            A readable approximation.
        """
        verb_words = re.sub(r"(?<!^)(?=[A-Z])", " ", frame_name).lower().strip()
        verb_words = verb_words.replace("_", " ")
        role_parts = [
            f"{role}: {self._realize_np(val, context, role_name=role)}"
            for role, val in roles.items()
            if role not in _INTERNAL_ROLES
        ]
        if role_parts:
            return f"{verb_words} ({', '.join(role_parts)})"
        return verb_words

    # -----------------------------------------------------------------------
    # Quantifier realization
    # -----------------------------------------------------------------------

    def _realize_quantifier(
        self,
        q: "Exists | ForAll | Iota",
        context: Context,
    ) -> str:
        """Realize Exists/ForAll/Iota as an English determiner phrase.

        Patterns:
          Iota(x, D, B)   → "the D" or "the D that Bs"
          Exists(x, D, B) → "some D" or "some D that Bs"
          ForAll(x, D, B) → "every D" or "every D that Bs"

        When the body is trivially just the bound variable, the relative
        clause is omitted.

        Args:
            q:       Quantifier term.
            context: Discourse context.

        Returns:
            A realized DP string.
        """
        domain_str = self.realize(q.domain, context)

        if isinstance(q, Iota):
            det = "the"
        elif isinstance(q, Exists):
            det = "some"
        else:  # ForAll
            det = "every"

        if self._is_trivial_body(q.body, q.var):
            return f"{det} {domain_str}"

        body_str = self.realize(q.body, context)
        return f"{det} {domain_str} that {body_str}"

    def _is_trivial_body(self, body: HLF, var: str) -> bool:
        """Return True if *body* is just Var(var) with no added restriction.

        Args:
            body: Quantifier body.
            var:  Bound variable name.

        Returns:
            True if body carries no information beyond naming the variable.
        """
        return isinstance(body, Var) and body.name == var

    # -----------------------------------------------------------------------
    # Connective realization
    # -----------------------------------------------------------------------

    def _realize_negation(self, n: NegTerm, context: Context) -> str:
        """Realize negation as a grammatical English negative clause.

        Prefers inserting "not" after an auxiliary for EventTerms,
        falling back to prepending "not" for other term types.

        Args:
            n:       The NegTerm.
            context: Discourse context.

        Returns:
            A realized negative string.
        """
        if isinstance(n.body, (EventTerm, FrameIntro)):
            inner = self._realize_event_term(n.body, context)
            return self._insert_negation(inner)
        inner = self.realize(n.body, context)
        return f"not {inner}"

    def _insert_negation(self, clause: str) -> str:
        """Insert "not" or "do not" into a verbal clause.

        Searches for the first auxiliary verb and inserts "not"
        immediately after it. If no auxiliary is found, uses
        "does not" + base VP.

        Args:
            clause: A realized verbal clause.

        Returns:
            The negated clause.
        """
        auxiliaries = {
            "is", "are", "was", "were", "am",
            "has", "have", "had",
            "will", "would", "can", "could",
            "may", "might", "shall", "should", "must",
        }
        words = clause.split()
        for i, word in enumerate(words):
            if word.lower() in auxiliaries:
                words.insert(i + 1, "not")
                return " ".join(words)
        # No auxiliary found
        if len(words) >= 2:
            subj = words[0]
            vp = " ".join(words[1:])
            return f"{subj} does not {vp}"
        return f"not {clause}"

    def _realize_conjunction(self, c: ConjTerm, context: Context) -> str:
        """Realize a conjunction with Oxford comma: "X, Y, and Z".

        Args:
            c:       ConjTerm.
            context: Discourse context.

        Returns:
            Comma-list with "and" before the last element.
        """
        if not c.conjuncts:
            return "nothing"
        parts = [self.realize(conj, context) for conj in c.conjuncts]
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]} and {parts[1]}"
        return ", ".join(parts[:-1]) + f", and {parts[-1]}"

    def _realize_disjunction(self, d: DisjTerm, context: Context) -> str:
        """Realize a disjunction: "X, Y, or Z".

        Args:
            d:       DisjTerm.
            context: Discourse context.

        Returns:
            Comma-list with "or" before the last element.
        """
        if not d.disjuncts:
            return "nothing"
        parts = [self.realize(disj, context) for disj in d.disjuncts]
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]} or {parts[1]}"
        return ", ".join(parts[:-1]) + f", or {parts[-1]}"

    # -----------------------------------------------------------------------
    # Modal realization
    # -----------------------------------------------------------------------

    def _realize_modal(self, modal_kind: str, inner_vp: str) -> str:
        """Prefix an inner VP with the appropriate modal auxiliary.

        Strips "does/do/did" helper verbs so modals take bare infinitives.

        Args:
            modal_kind: Modal type key.
            inner_vp:   The already-realized VP string.

        Returns:
            "[modal] [base VP]".
        """
        modal_word = _MODAL_WORDS.get(modal_kind, "might")
        # Remove leading do-support helpers
        vp = re.sub(r"^(does|do|did)\s+", "", inner_vp, flags=re.IGNORECASE)
        return f"{modal_word} {vp}"

    # -----------------------------------------------------------------------
    # Polyphony
    # -----------------------------------------------------------------------

    def _realize_poly(self, poly: PolyTerm, context: Context) -> str:
        """Select and realize the best-graded voice from a PolyTerm.

        Args:
            poly:    PolyTerm with multiple voices.
            context: Discourse context.

        Returns:
            Realization of the highest-graded voice.
        """
        if not poly.voices:
            return ""
        best = max(
            poly.voices,
            key=lambda v: (v.grade.value if v.grade else float("-inf")),
        )
        return self.realize(best, context)

    # -----------------------------------------------------------------------
    # Constant realization
    # -----------------------------------------------------------------------

    def _realize_const(self, c: Const, context: Context) -> str:
        """Realize a Const: pronominalize, use referent NP, or use name.

        Args:
            c:       Constant term.
            context: Discourse context.

        Returns:
            A realized noun phrase string.
        """
        name = c.name
        pronoun = self._pronominalize(name, context)
        if pronoun is not None:
            return pronoun
        ref = context.find_referent(name)
        if ref is not None:
            return self._np_from_referent(ref, context, "nominative")
        return self._add_article_if_needed(name.replace("_", " ").strip(), context)

    # -----------------------------------------------------------------------
    # NP realization
    # -----------------------------------------------------------------------

    def _realize_np(
        self,
        value: Any,
        context: Context,
        role_name: str = "",
    ) -> str:
        """Realize any HLF value as a noun phrase.

        Dispatches on the runtime type of *value*, handling HLF terms,
        plain strings, dicts, and RoleFiller-like objects.

        Args:
            value:     The value to realize.
            context:   Discourse context.
            role_name: Syntactic role (used for pronoun case selection).

        Returns:
            A realized NP string.
        """
        if value is None:
            return ""

        if isinstance(value, str):
            return self._add_article_if_needed(value, context)

        if isinstance(value, Const):
            return self._realize_const(value, context)

        if isinstance(value, Var):
            ref = context.find_referent(value.name)
            if ref is not None:
                case = "accusative" if role_name in _OBJECT_ROLES else "nominative"
                return self._np_from_referent(ref, context, case)
            return value.name.replace("_", " ")

        if isinstance(value, (Exists, ForAll, Iota)):
            return self._realize_quantifier(value, context)

        if isinstance(value, PolyTerm):
            return self._realize_poly(value, context)

        if isinstance(value, (EventTerm, FrameIntro)):
            return self._nominalize_event(value, context)

        if isinstance(value, ConjTerm):
            return self._realize_conjunction(value, context)

        if isinstance(value, DisjTerm):
            return self._realize_disjunction(value, context)

        if isinstance(value, HLF):
            return self.realize(value, context)

        if isinstance(value, dict):
            head = value.get("head", "")
            det = value.get("determiner", "")
            mods = value.get("modifiers", [])
            mod_strs = [
                (m.get("text", "") if isinstance(m, dict) else str(m))
                for m in mods
            ]
            parts = ([det] if det else []) + [s for s in mod_strs if s] + ([head] if head else [])
            return " ".join(parts) if parts else str(value)

        # Duck-typed RoleFiller
        if hasattr(value, "value") and hasattr(value, "head"):
            inner = value.value
            if isinstance(inner, HLF):
                return self._realize_np(inner, context, role_name)
            if isinstance(inner, str) and inner:
                return self._add_article_if_needed(inner, context)
            head = getattr(value, "head", "")
            if head:
                return self._add_article_if_needed(head, context)

        return str(value).replace("_", " ")

    def _add_article_if_needed(self, noun_str: str, context: Context) -> str:
        """Add a determiner to a bare noun if appropriate.

        Logic:
        1. If already has a determiner → leave as-is.
        2. If proper name (initial capital) → leave as-is.
        3. If mass noun → use "the" if known, else bare.
        4. Otherwise → use _determine_det() to get "the/a/an".

        Args:
            noun_str: The bare noun or NP string.
            context:  Discourse context.

        Returns:
            An NP string with appropriate determiner.
        """
        if not noun_str:
            return noun_str

        determiners: set[str] = {
            "the", "a", "an", "this", "that", "these", "those",
            "my", "your", "his", "her", "its", "our", "their",
            "every", "each", "some", "any", "no", "all",
        }
        first_word = noun_str.split()[0].lower() if noun_str.split() else ""
        if first_word in determiners:
            return noun_str

        # Proper name heuristic
        if noun_str[0].isupper():
            return noun_str

        # Mass noun: only use "the" if already mentioned
        base = noun_str.lower().rstrip("s")
        if noun_str.lower() in _MASS_NOUNS or base in _MASS_NOUNS:
            if context.find_referent(noun_str) is not None:
                return f"the {noun_str}"
            return noun_str

        det = self._determine_det(noun_str, context)
        return f"{det} {noun_str}"

    def _nominalize_event(
        self,
        et: "EventTerm | FrameIntro",
        context: Context,
    ) -> str:
        """Nominalize an embedded event as "the [V]-ing".

        Used when an EventTerm appears where an NP is expected (e.g.,
        as a role filler in another EventTerm).

        Args:
            et:      The event term to nominalize.
            context: Discourse context.

        Returns:
            A gerundial NP string.
        """
        frame_name = et.frame_type_name
        spec = FRAME_TO_VERB.get(frame_name)
        if spec is None:
            return "the " + frame_name.lower().replace("_", " ")
        verb = spec["verb"].split()[0]
        gerund = self._morph._make_present_participle(verb)
        return f"the {gerund}"

    def _realize_pp(self, prep: str, value: Any, context: Context) -> str:
        """Realize a prepositional phrase "[prep] [NP]".

        Args:
            prep:    The preposition string.
            value:   Object of the preposition.
            context: Discourse context.

        Returns:
            Realized PP string, or "" if the NP is empty.
        """
        np_str = self._realize_np(value, context)
        if not np_str:
            return ""
        return f"{prep} {np_str}"

    # -----------------------------------------------------------------------
    # Role-to-position helpers
    # -----------------------------------------------------------------------

    def _find_subject_role(
        self,
        spec: dict[str, Any],
        roles: dict[str, HLF],
    ) -> str:
        """Return the role name that maps to syntactic subject position.

        Searches the frame spec for a role with pos="subj", falls back
        to canonical subject-role names, then to the first available role.

        Args:
            spec:  Frame spec from FRAME_TO_VERB.
            roles: Available role fillers.

        Returns:
            Subject role name string.
        """
        for role, pos in spec.items():
            if pos == "subj" and role in roles:
                return role
        for role in _SUBJECT_ROLES:
            if role in roles:
                return role
        return next(iter(roles), "")

    def _find_object_role(
        self,
        spec: dict[str, Any],
        roles: dict[str, HLF],
        subj_role: str,
    ) -> Optional[str]:
        """Return the role name that maps to direct object position.

        Args:
            spec:      Frame spec.
            roles:     Available role fillers.
            subj_role: Subject role to exclude.

        Returns:
            Object role name, or None.
        """
        for role, pos in spec.items():
            if pos == "obj" and role != subj_role and role in roles:
                return role
        return None

    def _build_pp_adjuncts(
        self,
        spec: dict[str, Any],
        roles: dict[str, HLF],
        context: Context,
        subj_role: str,
        obj_role: Optional[str],
    ) -> list[str]:
        """Build all PP adjunct strings for roles not already realized.

        Iterates through the frame spec, collects roles with preposition
        positions, sorts them (non-temporal first), then realizes each
        as a PP. Also handles roles not in the spec via
        _guess_preposition.

        Args:
            spec:      Frame spec.
            roles:     Available role fillers.
            context:   Discourse context.
            subj_role: Subject role (skip).
            obj_role:  Direct object role (skip).

        Returns:
            List of realized PP strings.
        """
        skip: set[str] = {subj_role, obj_role or "", "verb", "copular"}
        prep_roles: list[tuple[str, str]] = []

        for role, pos in spec.items():
            if role in skip or role == "copular":
                continue
            if (
                isinstance(pos, str)
                and pos not in ("subj", "obj", "obj2", "adv")
                and role in roles
            ):
                prep_roles.append((role, pos))

        def _pp_key(item: tuple[str, str]) -> int:
            _, prep = item
            return 2 if prep in _TEMPORAL_PREPS else 0

        prep_roles.sort(key=_pp_key)
        pps: list[str] = []

        for role, prep in prep_roles:
            pp_str = self._realize_pp(prep, roles[role], context)
            if pp_str:
                pps.append(pp_str)

        # Roles present in the event but not mentioned in the spec
        spec_roles = set(spec.keys()) | {"copular"}
        for role, val in roles.items():
            if role in spec_roles or role in skip or role in _INTERNAL_ROLES:
                continue
            pp_str = self._realize_pp(self._guess_preposition(role), val, context)
            if pp_str:
                pps.append(pp_str)

        return pps

    def _guess_preposition(self, role_name: str) -> str:
        """Return a plausible preposition for a role not in FRAME_TO_VERB.

        Args:
            role_name: The role name.

        Returns:
            A preposition string.
        """
        preps: dict[str, str] = {
            "goal": "to", "source": "from", "location": "at",
            "place": "at", "time": "at", "manner": "in",
            "instrument": "with", "purpose": "for",
            "recipient": "to", "beneficiary": "for",
            "cause": "because of", "reason": "because of",
            "material": "from", "result": "into",
            "path": "along", "direction": "toward",
            "topic": "about", "theme": "about",
            "content": "about", "context": "in",
            "condition": "under", "standard": "according to",
            "medium": "through", "channel": "via",
            "duration": "for", "frequency": "with",
        }
        return preps.get(role_name, "with")

    # -----------------------------------------------------------------------
    # Determiner and pronoun selection
    # -----------------------------------------------------------------------

    def _choose_word_order(
        self,
        roles: dict[str, Any],
        info_structure: dict[str, Any],
    ) -> list[str]:
        """Determine clause-level linear order of role fillers.

        Implements canonical English SVO with optional topic fronting
        and focus-last ordering.

        Args:
            roles:          Role name → value dict.
            info_structure: Contains keys: "topic", "focus", "subject_role".

        Returns:
            Ordered role-name list with "__verb__" placeholder.
        """
        order: list[str] = []
        topic = info_structure.get("topic", "")
        focus = info_structure.get("focus", "")
        subj = info_structure.get("subject_role", "")

        if topic and topic != subj and topic in roles:
            order.append(topic)
        if subj and subj in roles and subj not in order:
            order.append(subj)
        order.append("__verb__")
        for role in _OBJECT_ROLES:
            if role in roles and role not in order:
                order.append(role)
                break
        if focus and focus in roles and focus not in order:
            order.append(focus)
        for role in roles:
            if role not in order:
                order.append(role)
        return order

    def _determine_det(self, referent_str: str, context: Context) -> str:
        """Choose "the" (already introduced) or "a"/"an" (new).

        Tracks introduced referents in self._introduced.

        Args:
            referent_str: Head noun string.
            context:      Discourse context.

        Returns:
            "the", "a", or "an".
        """
        normalized = referent_str.lower().strip()
        if normalized in self._introduced:
            return "the"
        if context.find_referent(referent_str) is not None:
            return "the"
        self._introduced.add(normalized)
        return self._morph.get_article(
            referent_str,
            MorphFeatures(definiteness="indefinite"),
        )

    def _pronominalize(
        self,
        referent_str: str,
        context: Context,
    ) -> Optional[str]:
        """Return a pronoun if pronominalization conditions are met.

        Conditions:
        1. Referent has been introduced (in self._introduced).
        2. Referent is the current topic OR has salience >= 0.6.

        Args:
            referent_str: Referent name.
            context:      Discourse context.

        Returns:
            Pronoun string, or None if conditions not met.
        """
        normalized = referent_str.lower().strip()
        if normalized not in self._introduced:
            return None
        ref = context.find_referent(referent_str)
        if ref is None:
            return None
        if context.topic and referent_str.lower() != context.topic.lower():
            sal = getattr(ref, "salience", 0.0)
            if sal < 0.6:
                return None
        feats = MorphFeatures(
            person=3,
            number=getattr(ref, "number", "singular"),
            gender=getattr(ref, "gender", "neuter"),
            case="nominative",
        )
        return self._morph.get_pronoun(feats)

    def _np_from_referent(
        self,
        ref: Referent,
        context: Context,
        case: str = "nominative",
    ) -> str:
        """Build an NP from a known discourse referent.

        Returns a pronoun on subsequent high-salience mentions, a full
        NP on first mention.

        Args:
            ref:     Discourse referent.
            context: Discourse context.
            case:    Grammatical case ("nominative", "accusative", "genitive").

        Returns:
            Pronoun or full NP string.
        """
        ref_id = getattr(ref, "referent_id", "")
        if ref_id in self._introduced and getattr(ref, "salience", 0.0) > 0.5:
            feats = MorphFeatures(
                person=3,
                number=getattr(ref, "number", "singular"),
                gender=getattr(ref, "gender", "neuter"),
                case=case,
            )
            return self._morph.get_pronoun(feats)
        if ref_id:
            self._introduced.add(ref_id)
        desc = getattr(ref, "description", ref_id or str(ref))
        det = self._determine_det(desc, context)
        return f"{det} {desc}" if det in ("the", "a", "an") else desc

    def _np_morph_features(
        self,
        subj_val: Optional[HLF],
        context: Context,
    ) -> MorphFeatures:
        """Derive morphological features from a subject value for agreement.

        Returns person/number/gender features based on:
        - Const → look up in context.referents
        - Var with known pronoun names → hardcoded features
        - ConjTerm with multiple conjuncts → plural
        - Default → 3rd-person singular

        Args:
            subj_val: The subject HLF value.
            context:  Discourse context.

        Returns:
            MorphFeatures for agreement.
        """
        feats = MorphFeatures(person=self._default_person, number=self._default_number)
        if subj_val is None:
            return feats

        if isinstance(subj_val, Const):
            ref = context.find_referent(subj_val.name)
            if ref is not None:
                return MorphFeatures(
                    person=3,
                    number=getattr(ref, "number", "singular"),
                    gender=getattr(ref, "gender", "neuter"),
                )

        if isinstance(subj_val, Var):
            n = subj_val.name.lower()
            if n == "i":
                return MorphFeatures(person=1, number="singular")
            if n == "we":
                return MorphFeatures(person=1, number="plural")
            if n == "you":
                return MorphFeatures(person=2, number="singular")
            if n == "they":
                return MorphFeatures(person=3, number="plural")

        if isinstance(subj_val, ConjTerm) and len(subj_val.conjuncts) > 1:
            return MorphFeatures(person=3, number="plural")

        if isinstance(subj_val, ForAll):
            # "every cat" → 3sg
            return MorphFeatures(person=3, number="singular")

        return feats

    def _np_phi_features(
        self,
        subj_val: Optional[HLF],
        context: Context,
    ) -> "PhiFeatures":
        """Derive PhiFeatures from subject HLF for Grade-weighted agreement.

        Uses the formal φ-feature system from features.py.
        Falls back to _np_morph_features when the DM stack is unavailable.
        """
        if not _HAS_DM:
            mf = self._np_morph_features(subj_val, context)
            return PhiFeatures(str(mf.person), "pl" if mf.number == "plural" else "sg")

        if subj_val is None:
            return PhiFeatures(str(self._default_person),
                             "pl" if self._default_number == "plural" else "sg")

        if isinstance(subj_val, Var):
            from gofai_chat.grammar.features import PRONOUN_PHI
            phi = PRONOUN_PHI.get(subj_val.name)
            if phi:
                return phi
            n = subj_val.name.lower()
            phi = PRONOUN_PHI.get(n)
            if phi:
                return phi

        if isinstance(subj_val, Const):
            ref = context.find_referent(subj_val.name)
            if ref is not None:
                num = "pl" if getattr(ref, "number", "singular") == "plural" else "sg"
                return PhiFeatures("3", num)

        if isinstance(subj_val, ConjTerm) and len(subj_val.conjuncts) > 1:
            return PhiFeatures("3", "pl")

        return PhiFeatures(str(self._default_person),
                         "pl" if self._default_number == "plural" else "sg")

    # -----------------------------------------------------------------------
    # Tense/aspect selection
    # -----------------------------------------------------------------------

    def _select_tense_form(
        self,
        lemma: str,
        tense: str,
        aspect: str,
    ) -> str:
        """Select the inflected form for a given tense/aspect.

        Uses DMEngine (Distributed Morphology) when available for
        Grade-weighted morphological realization.  Falls back to the
        legacy MorphologyEngine.

        Args:
            lemma:  Base verb form.
            tense:  Tense string.
            aspect: Aspect string.

        Returns:
            Inflected verb string.
        """
        if self._dm is not None and _HAS_DM:
            bundle = make_verb_features(
                tense=tense,
                aspect=aspect,
                person=str(self._default_person),
                number="pl" if self._default_number == "plural" else "sg",
            )
            form, grade = self._dm.realize(bundle, lemma)
            if grade.to_prob() > 0.3:
                return form
        # Fall back to legacy morphology engine
        feats = MorphFeatures(tense=tense, aspect=aspect, person=3, number="singular")
        return self._morph.conjugate(lemma, feats)

    def _select_tense_form_with_phi(
        self,
        lemma: str,
        tense: str,
        aspect: str,
        phi: "PhiFeatures",
    ) -> str:
        """Select inflected form using explicit PhiFeatures for agreement.

        Uses DMEngine with full φ-feature specification for proper
        subject-verb agreement (T[uφ] → values from SUBJ's φ-features).

        Args:
            lemma:  Base verb form.
            tense:  Tense string.
            aspect: Aspect string.
            phi:    φ-features from the subject.

        Returns:
            Inflected verb string.
        """
        if self._dm is not None and _HAS_DM:
            bundle = make_verb_features(
                tense=tense,
                aspect=aspect,
                person=phi.person,
                number=phi.number,
            )
            form, grade = self._dm.realize(bundle, lemma)
            if grade.to_prob() > 0.3:
                return form
        # Fallback
        person_int = int(phi.person) if _HAS_DM and hasattr(phi, 'person') else 3
        number_str = "plural" if (_HAS_DM and hasattr(phi, 'number') and phi.number == "pl") else "singular"
        feats = MorphFeatures(tense=tense, aspect=aspect,
                             person=person_int, number=number_str)
        return self._morph.conjugate(lemma, feats)

    # -----------------------------------------------------------------------
    # PP placement
    # -----------------------------------------------------------------------

    def _pp_placement(
        self,
        roles: dict[str, Any],
    ) -> dict[str, str]:
        """Annotate roles with pre/post VP placement.

        Args:
            roles: Role dictionary.

        Returns:
            Mapping role_name → "pre" or "post".
        """
        pre_roles = {"reason", "given_that", "condition", "topic_frame"}
        return {
            role: ("pre" if role in pre_roles else "post")
            for role in roles
        }

    # -----------------------------------------------------------------------
    # Linearization
    # -----------------------------------------------------------------------

    def linearize(self, tree: dict[str, Any]) -> str:
        """Join tree["parts"] into a normalized surface string.

        Filters empty strings, joins with spaces, normalizes whitespace,
        and fixes punctuation spacing.

        Args:
            tree: Dict with key "parts": list[str].

        Returns:
            A whitespace-normalized surface string.
        """
        parts = tree.get("parts", [])
        non_empty = [p for p in parts if isinstance(p, str) and p.strip()]
        text = " ".join(non_empty)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([,;:!?.])", r"\1", text)
        text = re.sub(r"([.!?])\s*[.!?]+", r"\1", text)
        return text

    # -----------------------------------------------------------------------
    # Capitalization / punctuation
    # -----------------------------------------------------------------------

    def _capitalize_and_punctuate(self, text: str) -> str:
        """Capitalize the first character and ensure terminal punctuation.

        Args:
            text: Realized surface string.

        Returns:
            A properly capitalized, punctuated sentence.
        """
        if not text:
            return text
        text = text.strip()
        text = text[0].upper() + text[1:]
        if text[-1] not in ".!?":
            text += "."
        return text

    # -----------------------------------------------------------------------
    # Additional helpers
    # -----------------------------------------------------------------------

    def _form_relative_clause(
        self,
        rel_event: HLF,
        antecedent: str,
        context: Context,
    ) -> str:
        """Form a relative clause for a head noun.

        Uses "who" for animate heads, "that" for inanimate.

        Args:
            rel_event:  Event to embed as relative clause.
            antecedent: Head noun being relativized.
            context:    Discourse context.

        Returns:
            Relative clause string starting with "who" or "that".
        """
        inner = self.realize(rel_event, context)
        animate_heads = {
            "person", "man", "woman", "child", "student", "teacher",
            "doctor", "worker", "citizen", "soldier", "artist", "scientist",
        }
        relativizer = "who" if antecedent.lower() in animate_heads else "that"
        return f"{relativizer} {inner}"

    def _coordination_reduction(self, sentences: list[str]) -> str:
        """Reduce coordinated sentences with the same subject to a VP conjunction.

        "Alice runs. Alice swims." → "Alice runs and swims."

        Args:
            sentences: List of sentence strings.

        Returns:
            Reduced or conjoined string.
        """
        if not sentences:
            return ""
        if len(sentences) == 1:
            return sentences[0]
        first_words = [s.split()[0].lower() if s.split() else "" for s in sentences]
        if len(set(first_words)) == 1 and first_words[0]:
            subject = sentences[0].split()[0]
            vps: list[str] = []
            for s in sentences:
                words = s.split()
                vp = " ".join(words[1:]).rstrip(".!?")
                if vp:
                    vps.append(vp)
            if len(vps) == 2:
                return f"{subject} {vps[0]} and {vps[1]}."
            if len(vps) > 2:
                return f"{subject} {', '.join(vps[:-1])}, and {vps[-1]}."
        return " ".join(sentences)

    def _extract_tense_from_wrapper(self, lf: HLF) -> tuple[str, str, HLF]:
        """Unwrap tense/aspect wrappers, returning (tense, aspect, inner).

        Args:
            lf: Possibly wrapped HLF.

        Returns:
            Tuple (tense, aspect, inner_lf).
        """
        tense = "present"
        aspect = "simple"
        current = lf
        while isinstance(current, (TenseTerm, AspectTerm, ModalTerm)):
            if isinstance(current, TenseTerm):
                tense = current.tense
                current = current.body
            elif isinstance(current, AspectTerm):
                aspect = current.aspect
                current = current.body
            else:
                current = current.body
        return tense, aspect, current

    def _possessive_np(self, possessor: str, possessed: str) -> str:
        """Form "[possessor]'s [possessed]".

        Args:
            possessor: Possessor NP string.
            possessed: Possessed noun string.

        Returns:
            Possessive NP.
        """
        return f"{possessor}' {possessed}" if possessor.endswith("s") else f"{possessor}'s {possessed}"

    def _is_mass_noun(self, noun: str) -> bool:
        """Return True if *noun* is likely a mass noun.

        Args:
            noun: Noun string.

        Returns:
            True if noun is in _MASS_NOUNS.
        """
        return noun.lower() in _MASS_NOUNS or noun.lower().rstrip("s") in _MASS_NOUNS

    def _is_proper_noun(self, name: str) -> bool:
        """Return True if *name* appears to be a proper noun.

        Uses the heuristic that all words are capitalized.

        Args:
            name: Noun string.

        Returns:
            True if likely proper.
        """
        words = name.split()
        return bool(words) and all(w[0].isupper() for w in words if w)

    def _number_from_np(self, np_val: Any, context: Context) -> str:
        """Extract grammatical number from an NP value.

        Args:
            np_val:  NP value (HLF, string, etc.).
            context: Discourse context.

        Returns:
            "singular" or "plural".
        """
        if isinstance(np_val, Const):
            ref = context.find_referent(np_val.name)
            if ref is not None:
                return getattr(ref, "number", "singular")
        if isinstance(np_val, ForAll):
            return "singular"
        if isinstance(np_val, ConjTerm) and len(np_val.conjuncts) > 1:
            return "plural"
        return "singular"

    def _build_sentence_plan(
        self,
        lf: HLF,
        context: Context,
        tense: str = "present",
        aspect: str = "simple",
    ) -> dict[str, Any]:
        """Build an internal sentence plan dict before linearization.

        Useful for downstream components that need structured access to
        sentence constituents rather than a flat string.

        Args:
            lf:      Event LF.
            context: Discourse context.
            tense:   Target tense string.
            aspect:  Target aspect string.

        Returns:
            Dict with keys: subject, verb, object, adjuncts, features.
        """
        plan: dict[str, Any] = {
            "subject": "",
            "verb": "",
            "object": "",
            "adjuncts": [],
            "features": {},
        }

        if not isinstance(lf, (EventTerm, FrameIntro)):
            plan["subject"] = self.realize(lf, context)
            return plan

        frame_name = lf.frame_type_name
        roles = lf.roles if isinstance(lf, EventTerm) else lf.role_fillers
        spec = FRAME_TO_VERB.get(frame_name, {})

        subj_role = self._find_subject_role(spec, roles)
        obj_role = self._find_object_role(spec, roles, subj_role)
        verb_lemma = spec.get("verb", frame_name.lower().replace("_", " "))

        subj_val = roles.get(subj_role)
        obj_val = roles.get(obj_role) if obj_role else None
        morph_feats = self._np_morph_features(subj_val, context)

        plan["subject"] = (
            self._realize_np(subj_val, context, subj_role)
            if subj_val is not None else "someone"
        )
        plan["verb"] = self._morph.conjugate(
            verb_lemma.split()[0],
            MorphFeatures(
                tense=tense, aspect=aspect,
                person=morph_feats.person, number=morph_feats.number,
            ),
        )
        plan["object"] = (
            self._realize_np(obj_val, context, obj_role or "")
            if obj_val is not None else ""
        )
        plan["adjuncts"] = self._build_pp_adjuncts(
            spec, roles, context, subj_role, obj_role
        )
        plan["features"] = {
            "person": morph_feats.person,
            "number": morph_feats.number,
            "gender": morph_feats.gender,
        }
        return plan


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def quick_realize(lf: HLF, context: Optional[Context] = None) -> str:
    """Quickly realize *lf* as a sentence, creating a Context if needed.

    Args:
        lf:      Logical form to realize.
        context: Optional discourse context.

    Returns:
        A capitalized, punctuated English sentence.
    """
    from gofai_chat.core.judgment import Context as _Context
    ctx = context if context is not None else _Context()
    return SurfaceRealizer().realize_sentence(lf, ctx)


# ---------------------------------------------------------------------------
# Demonstration
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from gofai_chat.core.judgment import Context

    ctx = Context()
    r = SurfaceRealizer()

    # Simple event
    eating = EventTerm(
        frame_type_name="Eating",
        event_var="e1",
        roles={"eater": Const(name="Mary"), "food": Const(name="fish")},
    )
    print(r.realize_sentence(eating, ctx))           # Mary eats fish.

    # Past tense
    r.reset()
    print(r.realize_sentence(TenseTerm(tense="past", body=eating), ctx))  # Mary ate fish.

    # Progressive
    r.reset()
    prog = TenseTerm(tense="present", body=AspectTerm(aspect="progressive", body=eating))
    print(r.realize_sentence(prog, ctx))             # Mary is eating fish.

    # Negation
    r.reset()
    print(r.realize_sentence(NegTerm(body=eating), ctx))  # Mary does not eat fish.

    # Modal
    r.reset()
    modal = ModalTerm(modal_kind="deontic_possible", body=eating)
    print(r.realize_sentence(modal, ctx))            # may eat fish.

    # Giving (ditransitive)
    r.reset()
    giving = EventTerm(
        frame_type_name="Giving",
        roles={
            "donor": Const(name="Alice"),
            "theme": Const(name="book"),
            "recipient": Const(name="Bob"),
        },
    )
    print(r.realize_sentence(giving, ctx))           # Alice gives a book to Bob.

    # Conjunction
    r.reset()
    running = EventTerm(frame_type_name="Running", roles={"agent": Const(name="John")})
    print(r.realize(ConjTerm(conjuncts=[eating, running]), ctx))

    # Universal quantifier
    r.reset()
    forall_lf = ForAll(
        var="x",
        domain=Const(name="student"),
        body=EventTerm(
            frame_type_name="Studying",
            roles={"student": Var(name="x"), "subject": Const(name="mathematics")},
        ),
    )
    print(r.realize_sentence(forall_lf, ctx))        # Every student that studies mathematics.

    # Copular: Being_Located
    r.reset()
    loc = EventTerm(
        frame_type_name="Being_Located",
        roles={"theme": Const(name="book"), "location": Const(name="table")},
    )
    print(r.realize_sentence(loc, ctx))              # The book is at a table.

    print("\nSurface realizer module loaded successfully.")
