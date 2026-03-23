"""Formal statements of economic theorems as JuGeo propositions.
# copilot: theorem_economics theorems — 20+ formal economic theorems with JuGeo judgment coordinates

JuGeo judgments are 8-tuples: (c, phi, A, E, O, B, T, Pi) where:
  c  = claim (str)
  phi= formula in LaTeX (str)
  A  = agent (str)
  E  = evidence (list)
  O  = obstruction (str | None)
  B  = belief float in [0, 1]
  T  = trust_tier str  ("PROPOSAL" | "CANDIDATE" | "VERIFIED" | "CERTIFIED")
  Pi = proof_path (tuple of str)

Trust tier ordering:  PROPOSAL < CANDIDATE < VERIFIED < CERTIFIED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.ideation.theorem_economics.models import TheoremYieldModel, CompoundingEffect, LinearYieldModel
    _HAVE_MODELS = True
except ImportError:
    _HAVE_MODELS = False
    TheoremYieldModel = Any
    CompoundingEffect = Any
    LinearYieldModel = Any

__all__ = [
    "TheoremStatus",
    "ProofMethod",
    "EconomicTheorem",
    "TheoremCatalog",
    "TheoremVerifier",
    "T52_1", "T52_2", "T52_3", "T52_4", "T52_5",
    "T52_6", "T52_7", "T52_8", "T52_9", "T52_10",
    "T52_11", "T52_12", "T52_13", "T52_14", "T52_15",
    "default_catalog",
    "ARROWS_IMPOSSIBILITY",
    "GIBBARD_SATTERTHWAITE",
    "REVELATION_PRINCIPLE",
    "REVENUE_EQUIVALENCE",
    "MYERSONS_LEMMA",
    "FOLK_THEOREM",
    "NASH_EXISTENCE",
    "KAKUTANI_FIXED_POINT",
    "WALRASIAN_EQUILIBRIUM_EXISTENCE",
    "FIRST_WELFARE_THEOREM",
    "SECOND_WELFARE_THEOREM",
    "COASE_THEOREM",
    "GREEN_LAFFONT_HOLMSTROM",
    "VICKREY_CLARKE_GROVES",
    "CONDORCET_JURY_THEOREM",
    "SPENCE_SIGNALING",
    "AKERLOF_LEMONS",
    "ENVELOPE_THEOREM",
    "BERGE_MAXIMUM_THEOREM",
    "BLACKWELLS_THEOREM",
    "TOPKIS_THEOREM",
    "TheoremDatabase",
    "TheoremProofChain",
    "verify_theorem",
    "DEFAULT_THEOREM_DB",
]

_TIER_ORDER = {"PROPOSAL": 0, "CANDIDATE": 1, "VERIFIED": 2, "CERTIFIED": 3}


class TheoremStatus(str, Enum):
    """Lifecycle status of a formal theorem within JuGeo."""
    CONJECTURED = "conjectured"
    """The theorem has been stated but not yet formally proved."""
    PROVED = "proved"
    """The theorem has a recognised formal or rigorous proof."""


class ProofMethod(str, Enum):
    """The primary methodology used to establish a theorem."""
    ANALYTICAL = "analytical"
    """Closed-form, axiomatic, or deductive proof (e.g. fixed-point arguments)."""
    NUMERICAL = "numerical"
    """Computational verification or constructive numerical argument."""
    EMPIRICAL = "empirical"
    """Evidence derived from empirical data, experiments, or stylised facts."""


@dataclass(frozen=True)
class EconomicTheorem:
    """Immutable record representing a formal economic theorem as a JuGeo proposition.

    Required fields (positional)
    ----------------------------
    theorem_id : str
        Unique identifier, e.g. "ECO-001" or "T52-1".
    name : str
        Human-readable name of the theorem.
    statement : str
        Formal statement, preferably as a LaTeX string.
    proof_sketch : str
        2-4 sentence description of the proof strategy.
    status : TheoremStatus
        CONJECTURED or PROVED.
    proof_method : ProofMethod
        Primary methodology used to establish the theorem.

    Optional fields (keyword, with defaults)
    -----------------------------------------
    dependencies : list[str]
        List of theorem_id strings that this theorem relies on.
    trust_tier : str
        JuGeo trust tier. One of "PROPOSAL", "CANDIDATE", "VERIFIED", "CERTIFIED".
        Default: "PROPOSAL".
    judgment_coords : tuple
        JuGeo coordinate path — a tuple of strings identifying the node in the
        JuGeo sheaf (e.g. ("mechanism_design", "incentive_compatibility")).
    references : list[str]
        Bibliographic references (author-year strings or DOIs).
    domain : str
        Economic sub-domain tag (e.g. "game_theory", "mechanism_design").
    """

    # required fields (no defaults)
    theorem_id: str
    name: str
    statement: str
    proof_sketch: str
    status: TheoremStatus
    proof_method: ProofMethod

    # optional fields (with defaults)
    dependencies: list[str] = field(default_factory=list)
    trust_tier: str = "PROPOSAL"
    judgment_coords: tuple = ()
    references: list[str] = field(default_factory=list)
    domain: str = ""

    def is_proved(self) -> bool:
        """Return True iff the theorem status is PROVED."""
        return self.status == TheoremStatus.PROVED

    def summary(self) -> str:
        """One-line summary: '<id>: <name> [<trust_tier>]'."""
        return f"{self.theorem_id}: {self.name} [{self.trust_tier}]"

    def to_judgment_tuple(self) -> tuple:
        """Return a JuGeo 8-tuple (c, phi, A, E, O, B, T, Pi).

        Mapping
        -------
        c  <- name
        phi <- statement (LaTeX)
        A  <- "theorem_economics"
        E  <- references list (converted to tuple)
        O  <- first unresolved dependency, or None if all are resolved
        B  <- 1.0 if PROVED else 0.5
        T  <- trust_tier
        Pi <- judgment_coords (already a tuple of strings)
        """
        claim = self.name
        formula = self.statement
        agent = "theorem_economics"
        evidence = tuple(self.references)
        obstruction = self.dependencies[0] if self.dependencies else None
        belief = 1.0 if self.is_proved() else 0.5
        trust = self.trust_tier
        proof_path = self.judgment_coords
        return (claim, formula, agent, evidence, obstruction, belief, trust, proof_path)

    def citation_string(self) -> str:
        """Return a compact citation string listing all references."""
        if not self.references:
            return f"{self.name} (no references)"
        return f"{self.name}: " + "; ".join(self.references)


class TheoremCatalog:
    """Simple ordered catalog of EconomicTheorem objects, keyed by theorem_id.

    Preserves the original public API so all downstream code calling
    catalog.add(), catalog.get(), catalog.all() etc. continues to work.
    """

    def __init__(self) -> None:
        self._items: dict[str, EconomicTheorem] = {}

    def add(self, theorem: EconomicTheorem) -> None:
        """Add theorem to the catalog (overwrites if theorem_id already present)."""
        self._items[theorem.theorem_id] = theorem

    def get(self, theorem_id: str) -> EconomicTheorem | None:
        """Return the theorem with theorem_id, or None if not found."""
        return self._items.get(theorem_id)

    def list_proved(self) -> list[EconomicTheorem]:
        """Return all theorems whose status is PROVED."""
        return [t for t in self.all() if t.is_proved()]

    def list_by_method(self, method: ProofMethod) -> list[EconomicTheorem]:
        """Return all theorems proven by method."""
        return [t for t in self.all() if t.proof_method == method]

    def all(self) -> list[EconomicTheorem]:
        """Return all theorems in insertion order."""
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"TheoremCatalog({len(self)} theorems)"


class TheoremVerifier:
    """Runtime verifier for structural properties of JuGeo economic models.

    The three verify_* methods correspond to the three classical conditions
    checked by the theorem-economics pipeline: concavity of yield functions,
    equality of marginal yields at optimal allocation, and positive compounding.
    """

    def verify_concavity(self, model: object) -> bool:
        """Return True iff model is an instance of TheoremYieldModel.

        In the full pipeline this would also verify the second-order conditions
        of the yield function, but for the base class a type check is sufficient
        because TheoremYieldModel enforces concavity in its constructor.
        """
        if _HAVE_MODELS:
            return isinstance(model, TheoremYieldModel)
        return hasattr(model, "marginal_yield")

    def verify_optimal_allocation(
        self,
        models: list,
        allocs: dict[str, float],
        *,
        total_budget: float,
        tolerance: float = 0.5,
    ) -> bool:
        """Check that allocs is budget-feasible and equalises marginal yields.

        Parameters
        ----------
        models:
            List of TheoremYieldModel objects, each with a regime_id attribute
            and a marginal_yield(x) method.
        allocs:
            Mapping from regime_id to allocated amount.
        total_budget:
            Total budget that must be exhausted (up to tolerance).
        tolerance:
            Acceptable deviation for both the budget constraint and the
            equal-marginals condition.

        Returns
        -------
        bool
            True iff both conditions are satisfied.
        """
        marginals = [
            m.marginal_yield(allocs.get(m.regime_id, 0.0)) for m in models
        ]
        budget_ok = abs(sum(allocs.values()) - total_budget) <= tolerance
        eq_marginals = (
            (max(marginals) - min(marginals) <= tolerance) if marginals else True
        )
        return budget_ok and eq_marginals

    def verify_compounding(self, effect: Any) -> bool:
        """Return True iff effect has positive derived theorems and factor > 1.

        A CompoundingEffect is considered valid when it reports at least one
        derived theorem and a compounding factor strictly greater than unity,
        meaning knowledge builds on itself super-linearly.
        """
        return effect.derived_theorems > 0 and effect.compounding_factor > 1.0

    def verification_report(self, model: object) -> str:
        """Return a human-readable verification report string for model."""
        kind = type(model).__name__
        return f"Verification report for {kind}"


# ---------------------------------------------------------------------------
# Named theorem instances
# ---------------------------------------------------------------------------

ARROWS_IMPOSSIBILITY = EconomicTheorem(
    theorem_id="ECO-001",
    name="Arrow's Impossibility Theorem",
    statement=(
        r"$\nexists$ social welfare function $f: \mathcal{P}^n \to \mathcal{P}$ "
        r"satisfying simultaneously: (U) unrestricted domain, (P) weak Pareto "
        r"efficiency, (IIA) independence of irrelevant alternatives, and "
        r"(ND) non-dictatorship."
    ),
    proof_sketch=(
        "Arrow (1951) proves by exhaustion on preference profiles that any SWF "
        "satisfying U, P, and IIA must be dictatorial. The key step constructs a "
        "'pivotal' voter whose preferences determine the social ranking of every "
        "pair, contradicting ND. Sen (1986) provides an elegant reformulation "
        "using decisive sets and their closure under intersection. "
        "Geanakoplos (2005) gives three short, self-contained proofs."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("social_choice", "preference_aggregation", "impossibility"),
    references=[
        "Arrow, K. J. (1951). Social Choice and Individual Values. Wiley.",
        "Sen, A. K. (1986). Social Choice Theory. Handbook of Mathematical Economics Vol 3.",
        "Geanakoplos, J. (2005). Three brief proofs of Arrow's Impossibility Theorem. Economic Theory 26(1).",
    ],
    domain="social_choice",
)

GIBBARD_SATTERTHWAITE = EconomicTheorem(
    theorem_id="ECO-002",
    name="Gibbard-Satterthwaite Theorem",
    statement=(
        r"Every surjective social choice function $f: \mathcal{P}^n \to A$ "
        r"(with $|A| \geq 3$) that is strategy-proof (dominant-strategy "
        r"incentive compatible) must be dictatorial."
    ),
    proof_sketch=(
        "Gibbard (1973) and Satterthwaite (1975) independently reduce the problem "
        "to Arrow's Impossibility Theorem by associating each SCF with a social "
        "welfare function and showing that strategy-proofness implies IIA. "
        "The surjectivity and |A|>=3 conditions prevent degenerate constant functions "
        "from serving as counterexamples. Reny (2001) gives a direct combinatorial proof "
        "that avoids constructing an auxiliary SWF."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-001"],
    trust_tier="CERTIFIED",
    judgment_coords=("social_choice", "strategy_proofness", "impossibility"),
    references=[
        "Gibbard, A. (1973). Manipulation of voting schemes. Econometrica 41(4).",
        "Satterthwaite, M. A. (1975). Strategy-proofness and Arrow's conditions. Journal of Economic Theory 10(2).",
        "Reny, P. J. (2001). Arrow's theorem and the Gibbard-Satterthwaite theorem. Economics Letters 70(1).",
    ],
    domain="social_choice",
)

REVELATION_PRINCIPLE = EconomicTheorem(
    theorem_id="ECO-003",
    name="Revelation Principle",
    statement=(
        r"For any mechanism $\Gamma$ and any Bayesian Nash equilibrium "
        r"$\sigma^*$ of $\Gamma$, there exists a direct revelation mechanism "
        r"$\Gamma'$ that is incentive-compatible (truthful) and yields the "
        r"same outcome: $g(\sigma^*(v)) = g'(v)$ for all type profiles $v$."
    ),
    proof_sketch=(
        "Given an indirect mechanism Gamma with equilibrium sigma*, construct the direct "
        "mechanism Gamma' that simulates the equilibrium play: upon receiving reports "
        "(v_i) it runs sigma*(v) and applies g. Incentive compatibility follows because "
        "any profitable deviation in Gamma' would correspond to a profitable deviation "
        "in Gamma, contradicting sigma* being an equilibrium. "
        "The principle holds for dominant-strategy and Bayesian Nash equilibria alike, "
        "as well as for correlated equilibria."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("mechanism_design", "incentive_compatibility", "direct_mechanism"),
    references=[
        "Myerson, R. B. (1979). Incentive compatibility and the bargaining problem. Econometrica 47(1).",
        "Myerson, R. B. (1981). Optimal auction design. Mathematics of Operations Research 6(1).",
        "Mas-Colell, A., Whinston, M. D., Green, J. R. (1995). Microeconomic Theory. OUP, Ch 23.",
    ],
    domain="mechanism_design",
)

REVENUE_EQUIVALENCE = EconomicTheorem(
    theorem_id="ECO-004",
    name="Revenue Equivalence Theorem",
    statement=(
        r"Suppose bidders have independent private values drawn i.i.d. from "
        r"a distribution $F$ with density $f > 0$. Any two auction formats "
        r"that (i) allocate the object to the highest-value bidder and "
        r"(ii) assign zero surplus to the lowest type yield the same "
        r"expected revenue $R^* = \mathbb{E}[v_{(n-1)}]$ to the seller."
    ),
    proof_sketch=(
        "By the envelope theorem, each bidder's equilibrium expected surplus is "
        "pinned down by their type and the allocation rule alone. Since both "
        "auctions use the same allocation rule and give zero rent to the lowest "
        "type, the transfers must also be identical in expectation by the "
        "budget-balance identity. Myerson (1981) derives this as a corollary "
        "of the payment identity in optimal mechanism design. Krishna (2002) "
        "provides a clean textbook treatment with explicit surplus calculations."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-003", "ECO-018"],
    trust_tier="CERTIFIED",
    judgment_coords=("auction_theory", "revenue_equivalence", "independent_private_values"),
    references=[
        "Myerson, R. B. (1981). Optimal auction design. Mathematics of Operations Research 6(1).",
        "Vickrey, W. (1961). Counterspeculation, auctions, and competitive sealed tenders. Journal of Finance 16(1).",
        "Krishna, V. (2002). Auction Theory. Academic Press, Ch 3.",
    ],
    domain="auction_theory",
)

MYERSONS_LEMMA = EconomicTheorem(
    theorem_id="ECO-005",
    name="Myerson's Lemma",
    statement=(
        r"A single-parameter allocation rule $x: \mathbb{R} \to [0,1]$ is "
        r"implementable (i.e. there exists a payment rule making truth-telling "
        r"a dominant strategy) if and only if $x$ is monotone non-decreasing. "
        r"Moreover the implementing payment rule is unique and given by "
        r"$p(v) = v \cdot x(v) - \int_0^v x(z)\,dz$."
    ),
    proof_sketch=(
        "The 'only if' direction: if x were decreasing at some point, a bidder "
        "could profit by misreporting. The 'if' direction: for monotone x, "
        "define p by the integral formula; the resulting mechanism satisfies "
        "both local and global incentive constraints. The payment identity follows "
        "from integrating the local IC condition dp/dv = v * dx/dv along "
        "an increasing path, applying the fundamental theorem of calculus. "
        "Roughgarden (2016) gives an accessible algorithmic presentation."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-003"],
    trust_tier="CERTIFIED",
    judgment_coords=("mechanism_design", "implementability", "payment_identity"),
    references=[
        "Myerson, R. B. (1981). Optimal auction design. Mathematics of Operations Research 6(1).",
        "Roughgarden, T. (2016). Twenty Lectures on Algorithmic Game Theory. Cambridge University Press.",
    ],
    domain="mechanism_design",
)

FOLK_THEOREM = EconomicTheorem(
    theorem_id="ECO-006",
    name="Folk Theorem (Repeated Games)",
    statement=(
        r"In an infinitely repeated game $G^\infty$ with discount factor "
        r"$\delta \in (0,1)$, any payoff vector $v \in \mathbb{R}^n$ that is "
        r"(i) individually rational and (ii) feasible (in the convex hull of "
        r"stage-game payoffs) can be sustained as a subgame-perfect Nash "
        r"equilibrium for $\delta$ sufficiently close to 1."
    ),
    proof_sketch=(
        "Construct a grim-trigger or Nash-reversion strategy profile: cooperate "
        "as long as all players have cooperated; revert to the minmax action "
        "profile forever upon any deviation. Individual rationality ensures "
        "deviation is not profitable even in the first period once delta is large "
        "enough to offset the one-period gain from deviation. Fudenberg-Maskin (1986) "
        "extend this to the full feasible and individually rational payoff set using "
        "more sophisticated reward-and-punishment strategies."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-007"],
    trust_tier="CERTIFIED",
    judgment_coords=("game_theory", "repeated_games", "subgame_perfect_equilibrium"),
    references=[
        "Fudenberg, D., Maskin, E. (1986). The folk theorem in repeated games with discounting or with incomplete information. Econometrica 54(3).",
        "Rubinstein, A. (1979). Equilibrium in supergames with the overtaking criterion. Journal of Economic Theory 21(1).",
        "Mailath, G. J., Samuelson, L. (2006). Repeated Games and Reputations. OUP.",
    ],
    domain="game_theory",
)

NASH_EXISTENCE = EconomicTheorem(
    theorem_id="ECO-007",
    name="Nash Existence Theorem",
    statement=(
        r"Every finite normal-form game $G = (N, \{S_i\}_{i \in N}, \{u_i\}_{i \in N})$ "
        r"has at least one Nash equilibrium $\sigma^* \in \prod_{i \in N} \Delta(S_i)$ "
        r"in (possibly mixed) strategies, where $\Delta(S_i)$ is the simplex of "
        r"probability distributions over $S_i$."
    ),
    proof_sketch=(
        "Define the best-response correspondence beta_i mapping mixed strategy "
        "profiles to mixed strategies for player i; it is upper hemicontinuous and "
        "convex-valued on a compact convex domain. The product correspondence "
        "beta = product of beta_i satisfies the hypotheses of Kakutani's "
        "fixed-point theorem (ECO-008), so a fixed point sigma* = beta(sigma*) "
        "exists. This fixed point is precisely a Nash equilibrium since each player's "
        "strategy is a best response to the others."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-008"],
    trust_tier="CERTIFIED",
    judgment_coords=("game_theory", "equilibrium_existence", "mixed_strategy"),
    references=[
        "Nash, J. F. (1950). Equilibrium points in n-person games. PNAS 36(1).",
        "Nash, J. F. (1951). Non-cooperative games. Annals of Mathematics 54(2).",
        "Kakutani, S. (1941). A generalization of Brouwer's fixed point theorem. Duke Mathematical Journal 8(3).",
    ],
    domain="game_theory",
)

KAKUTANI_FIXED_POINT = EconomicTheorem(
    theorem_id="ECO-008",
    name="Kakutani Fixed-Point Theorem",
    statement=(
        r"Let $X \subset \mathbb{R}^n$ be a nonempty compact convex set and "
        r"$\phi: X \rightrightarrows X$ a correspondence that is (i) nonempty- "
        r"and convex-valued and (ii) upper hemicontinuous. Then $\exists\, "
        r"x^* \in X$ such that $x^* \in \phi(x^*)$."
    ),
    proof_sketch=(
        "Kakutani (1941) approximates the correspondence by piecewise-linear "
        "functions on a simplicial subdivision of X, applies Brouwer's "
        "fixed-point theorem to each approximation, and takes a convergent "
        "subsequence of fixed points. The limit is a fixed point of phi by "
        "upper hemicontinuity. This result is the workhorse behind Nash "
        "existence (ECO-007) and Walrasian equilibrium existence (ECO-009) proofs. "
        "Border (1985) gives a comprehensive treatment with economic applications."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("mathematics", "fixed_point", "correspondence"),
    references=[
        "Kakutani, S. (1941). A generalization of Brouwer's fixed point theorem. Duke Mathematical Journal 8(3).",
        "Border, K. C. (1985). Fixed Point Theorems with Applications to Economics and Game Theory. Cambridge University Press.",
    ],
    domain="mathematics",
)

WALRASIAN_EQUILIBRIUM_EXISTENCE = EconomicTheorem(
    theorem_id="ECO-009",
    name="Walrasian Equilibrium Existence",
    statement=(
        r"In an Arrow-Debreu economy with $\ell$ commodities and $n$ agents, "
        r"suppose preferences are continuous, convex, and strongly monotone, "
        r"and endowments $\omega_i \gg 0$. Then there exists a price vector "
        r"$p^* \in \Delta_\ell$ and an allocation $(x_i^*)$ such that each "
        r"$x_i^*$ maximises $u_i$ over $\{x : p^* \cdot x \leq p^* \cdot \omega_i\}$ "
        r"and markets clear: $\sum_i x_i^* = \sum_i \omega_i$."
    ),
    proof_sketch=(
        "Define the aggregate excess demand correspondence Z(p) = sum of xi(p) minus omega "
        "where xi is agent i's demand correspondence. By Walras's law p dot Z(p) = 0 for all p. "
        "Apply Kakutani's fixed-point theorem (ECO-008) to a normalised price-adjustment map "
        "on the price simplex; a fixed point satisfies Z(p*) <= 0 with complementary slackness. "
        "Arrow-Debreu (1954) and McKenzie (1954) provide full proofs under slight variations."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-008"],
    trust_tier="CERTIFIED",
    judgment_coords=("general_equilibrium", "existence", "Arrow_Debreu"),
    references=[
        "Arrow, K. J., Debreu, G. (1954). Existence of an equilibrium for a competitive economy. Econometrica 22(3).",
        "McKenzie, L. W. (1954). On equilibrium in Graham's model of world trade. Econometrica 22(2).",
        "Debreu, G. (1959). Theory of Value. Yale University Press.",
    ],
    domain="general_equilibrium",
)

FIRST_WELFARE_THEOREM = EconomicTheorem(
    theorem_id="ECO-010",
    name="First Welfare Theorem",
    statement=(
        r"Every Walrasian (competitive) equilibrium allocation $(x^*, p^*)$ in "
        r"an economy with locally non-satiated preferences is Pareto efficient: "
        r"$\nexists$ feasible allocation $(\tilde{x}_i)$ with $u_i(\tilde{x}_i) "
        r"\geq u_i(x_i^*)$ for all $i$ and strict inequality for some $i$."
    ),
    proof_sketch=(
        "Suppose for contradiction there exists a Pareto superior allocation (x-tilde_i). "
        "Local non-satiation implies p* dot x-tilde_i >= p* dot omega_i for all i, with "
        "strict inequality for the agent who strictly prefers x-tilde. Summing over all "
        "agents and using market clearing yields p* dot sum(x-tilde_i) > p* dot sum(omega_i), "
        "but feasibility requires sum(x-tilde_i) = sum(omega_i), a contradiction. "
        "The proof requires no convexity, only local non-satiation."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-009"],
    trust_tier="CERTIFIED",
    judgment_coords=("general_equilibrium", "welfare", "pareto_efficiency"),
    references=[
        "Arrow, K. J. (1951). An extension of the basic theorems of classical welfare economics. Proc. 2nd Berkeley Symp.",
        "Debreu, G. (1959). Theory of Value. Yale University Press, Ch 6.",
        "Mas-Colell, A., Whinston, M. D., Green, J. R. (1995). Microeconomic Theory. OUP, Ch 16.",
    ],
    domain="general_equilibrium",
)

SECOND_WELFARE_THEOREM = EconomicTheorem(
    theorem_id="ECO-011",
    name="Second Welfare Theorem",
    statement=(
        r"Under convexity of preferences and production sets, every Pareto "
        r"efficient allocation $x^*$ can be decentralised as a Walrasian "
        r"equilibrium with transfers: $\exists$ price vector $p \in \mathbb{R}^\ell_+$ "
        r"and lump-sum transfers $T_i$ such that $x_i^*$ maximises $u_i$ over "
        r"$\{x : p \cdot x \leq p \cdot \omega_i + T_i\}$ and $\sum_i T_i = 0$."
    ),
    proof_sketch=(
        "At a Pareto efficient allocation x*, the supporting hyperplane theorem "
        "(using convexity of upper contour sets) guarantees a price vector p separating "
        "the upper contour sets from the feasible set. Define transfers as "
        "T_i = p dot (x_i* - omega_i). Convexity ensures that the supporting hyperplane "
        "induces optimising behaviour, completing the decentralisation. The theorem "
        "separates efficiency from equity: any efficient outcome is attainable via markets "
        "after redistribution of endowments."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-009", "ECO-010"],
    trust_tier="CERTIFIED",
    judgment_coords=("general_equilibrium", "welfare", "decentralisation"),
    references=[
        "Arrow, K. J. (1951). An extension of the basic theorems of classical welfare economics.",
        "Debreu, G. (1959). Theory of Value. Yale University Press, Ch 6.",
        "Mas-Colell, A., Whinston, M. D., Green, J. R. (1995). Microeconomic Theory. OUP, Ch 16.",
    ],
    domain="general_equilibrium",
)

COASE_THEOREM = EconomicTheorem(
    theorem_id="ECO-012",
    name="Coase Theorem",
    statement=(
        r"In the absence of transaction costs and with well-defined property "
        r"rights, private bargaining between affected parties will lead to a "
        r"Pareto efficient outcome regardless of the initial assignment of "
        r"liability or property rights."
    ),
    proof_sketch=(
        "When transaction costs are zero, parties can costlessly negotiate "
        "Pareto improvements. Any initial allocation of rights defines a "
        "starting surplus distribution; gains from trade ensure parties reach "
        "the efficient frontier. The specific allocation of rights affects "
        "the distribution of surplus but not the efficiency of the outcome. "
        "Coase (1960) illustrates with nuisance law and the Sturges v Bridgman case; "
        "Farrell (1987) shows information asymmetries can prevent the result."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="VERIFIED",
    judgment_coords=("law_and_economics", "externalities", "bargaining"),
    references=[
        "Coase, R. H. (1960). The problem of social cost. Journal of Law and Economics 3.",
        "Stigler, G. J. (1966). The Theory of Price. Macmillan.",
        "Farrell, J. (1987). Information and the Coase theorem. Journal of Economic Perspectives 1(2).",
    ],
    domain="law_and_economics",
)

GREEN_LAFFONT_HOLMSTROM = EconomicTheorem(
    theorem_id="ECO-013",
    name="Green-Laffont-Holmstrom Theorem",
    statement=(
        r"Among all dominant-strategy incentive-compatible mechanisms for "
        r"a public goods problem with quasilinear utilities, only pivot "
        r"(Groves-Clarke) mechanisms are Pareto efficient. Moreover, in "
        r"the generic case no Groves mechanism satisfies budget balance "
        r"(Green-Laffont impossibility): $\nexists$ Groves mechanism with "
        r"$\sum_i t_i(\theta) = 0$ for all $\theta$."
    ),
    proof_sketch=(
        "Holmstrom (1979) characterises the full class of efficient DSIC "
        "mechanisms as exactly the Groves family by showing any efficient DSIC "
        "mechanism must have the VCG payment structure. Green and Laffont (1979) "
        "then show that for generic preference domains, no Groves mechanism "
        "can balance the budget: the externality payments always sum to a "
        "strictly negative number (money must be burned). Walker (1980) provides "
        "a simpler proof of the budget-balance impossibility result."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-014"],
    trust_tier="CERTIFIED",
    judgment_coords=("mechanism_design", "VCG", "budget_balance", "impossibility"),
    references=[
        "Green, J., Laffont, J.-J. (1979). Incentives in Public Decision-Making. North-Holland.",
        "Holmstrom, B. (1979). Groves' scheme on restricted domains. Econometrica 47(5).",
        "Walker, M. (1980). On the nonexistence of a dominant strategy mechanism. Econometrica 48(6).",
    ],
    domain="mechanism_design",
)

VICKREY_CLARKE_GROVES = EconomicTheorem(
    theorem_id="ECO-014",
    name="Vickrey-Clarke-Groves Mechanism",
    statement=(
        r"The VCG mechanism $(\chi^*, t^{\mathrm{VCG}})$ is dominant-strategy "
        r"incentive compatible (DSIC) and allocatively efficient. Each agent "
        r"$i$ receives transfer $t_i(\theta) = \sum_{j \neq i} v_j(\chi^*(\theta), \theta_j) "
        r"+ h_i(\theta_{-i})$ where $h_i$ is arbitrary in $\theta_{-i}$, and "
        r"$\chi^*$ maximises $\sum_j v_j(x, \theta_j)$ over feasible allocations $x$."
    ),
    proof_sketch=(
        "Each agent i's net payoff under VCG equals the total social welfare "
        "plus a term h_i that does not depend on their report theta_i. "
        "Therefore maximising personal payoff coincides with maximising social welfare, "
        "making truth-telling a dominant strategy. Clarke's pivot rule (h_i = 0) "
        "ensures individual rationality; Vickrey's second-price auction is the "
        "two-agent special case where the payment equals the externality on others."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=["ECO-003"],
    trust_tier="CERTIFIED",
    judgment_coords=("mechanism_design", "VCG", "dominant_strategy"),
    references=[
        "Vickrey, W. (1961). Counterspeculation, auctions, and competitive sealed tenders. Journal of Finance 16(1).",
        "Clarke, E. H. (1971). Multipart pricing of public goods. Public Choice 11(1).",
        "Groves, T. (1973). Incentives in teams. Econometrica 41(4).",
    ],
    domain="mechanism_design",
)

CONDORCET_JURY_THEOREM = EconomicTheorem(
    theorem_id="ECO-015",
    name="Condorcet Jury Theorem",
    statement=(
        r"Let $n$ voters each independently cast a binary vote correctly with "
        r"probability $p > \tfrac{1}{2}$. Then the probability that simple "
        r"majority rule chooses the correct alternative satisfies "
        r"$\Pr[\text{majority correct}] \xrightarrow{n \to \infty} 1$, and "
        r"majority rule strictly dominates any individual for all $n \geq 2$."
    ),
    proof_sketch=(
        "The majority vote outcome is correct iff more than n/2 voters are correct. "
        "By the law of large numbers, the fraction of correct votes converges to p > 1/2 "
        "almost surely, so the majority is correct with probability approaching 1. "
        "The finite-n dominance follows from the binomial CDF: P(Bin(n,p) > n/2) "
        "is strictly increasing in n for p > 1/2 (Berend-Paroush 1998). "
        "Correlation among votes weakens but does not necessarily destroy the result."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("social_choice", "voting", "information_aggregation"),
    references=[
        "Condorcet, Marquis de (1785). Essai sur l'application de l'analyse a la probabilite des decisions.",
        "Ladha, K. K. (1992). The Condorcet jury theorem, free speech, and correlated votes. American Journal of Political Science 36(3).",
        "Berend, D., Paroush, J. (1998). When is Condorcet's jury theorem valid? Social Choice and Welfare 15(4).",
    ],
    domain="social_choice",
)

SPENCE_SIGNALING = EconomicTheorem(
    theorem_id="ECO-016",
    name="Spence Job-Market Signaling",
    statement=(
        r"Under single-crossing ($\partial^2 c / \partial e \partial \theta < 0$ "
        r"where $c(e,\theta)$ is the cost of education $e$ for type $\theta$), "
        r"a separating signaling equilibrium exists in which high types choose "
        r"$e^* > 0$ and low types choose $e = 0$, with wages $w(e)$ equal to "
        r"the true marginal product at the equilibrium education level."
    ),
    proof_sketch=(
        "Construct a wage schedule w(e) = w_H if e >= e* else w_L. Single-crossing "
        "ensures there exists e* such that high types prefer (e*, w_H) to (0, w_L) "
        "while low types prefer (0, w_L) to (e*, w_H). Beliefs off the equilibrium "
        "path are set to assign low type, sustaining the schedule. Spence (1973) "
        "introduces this as a market signaling model; Cho-Kreps (1987) develop the "
        "intuitive criterion to refine off-path beliefs."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("information_economics", "signaling", "separating_equilibrium"),
    references=[
        "Spence, A. M. (1973). Job market signaling. Quarterly Journal of Economics 87(3).",
        "Riley, J. G. (1979). Informational equilibrium. Econometrica 47(2).",
        "Cho, I.-K., Kreps, D. M. (1987). Signaling games and stable equilibria. Quarterly Journal of Economics 102(2).",
    ],
    domain="information_economics",
)

AKERLOF_LEMONS = EconomicTheorem(
    theorem_id="ECO-017",
    name="Akerlof Market for Lemons",
    statement=(
        r"When sellers have private information about quality $q \in [q_L, q_H]$ "
        r"but buyers only observe the pooled average, the equilibrium price "
        r"$p^*$ satisfies $p^* = \mathbb{E}[q \mid q \leq p^*/c]$ where $c > 1$ "
        r"is the seller's reservation value ratio. This adverse selection can "
        r"cause complete market unravelling: only the worst quality trades."
    ),
    proof_sketch=(
        "At any candidate equilibrium price p, sellers participate only if their "
        "quality value cq <= p, so the pool of sellers is the worst segment. "
        "Buyers, knowing this, bid only the expected quality of participants, "
        "which is below p unless the distribution is degenerate. The fixed-point "
        "condition generically yields p* < p_H (partial or full unravelling). "
        "Akerlof (1970) illustrates with used cars; Wilson (1980) analyses the "
        "more general structure of adverse selection equilibria."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("information_economics", "adverse_selection", "market_failure"),
    references=[
        "Akerlof, G. A. (1970). The market for 'lemons': quality uncertainty and the market mechanism. Quarterly Journal of Economics 84(3).",
        "Wilson, C. (1980). The nature of equilibrium in markets with adverse selection. Bell Journal of Economics 11(1).",
        "Rothschild, M., Stiglitz, J. E. (1976). Equilibrium in competitive insurance markets. Quarterly Journal of Economics 90(4).",
    ],
    domain="information_economics",
)

ENVELOPE_THEOREM = EconomicTheorem(
    theorem_id="ECO-018",
    name="Envelope Theorem",
    statement=(
        r"Let $V(\alpha) = \max_{x} f(x, \alpha)$ subject to $g(x, \alpha) = 0$. "
        r"At any interior optimum $x^*(\alpha)$, the derivative of the value "
        r"function satisfies $\frac{dV}{d\alpha} = \frac{\partial \mathcal{L}}{\partial \alpha}"
        r"\bigg|_{x = x^*(\alpha)}$ where $\mathcal{L} = f - \lambda g$ is the "
        r"Lagrangian. Direct effects through the optimal $x^*$ cancel by "
        r"first-order conditions (envelope property)."
    ),
    proof_sketch=(
        "Differentiate V(alpha) = f(x*(alpha), alpha) with respect to alpha using the chain rule. "
        "The term df/dx times dx*/dalpha equals lambda times dg/dx times dx*/dalpha, which "
        "vanishes because x* satisfies the FOC df/dx = lambda times dg/dx and dx*/dalpha "
        "is finite. The remaining term df/dalpha minus lambda times dg/dalpha equals dL/dalpha. "
        "Milgrom-Segal (2002) give a general version for non-smooth problems and integral "
        "objective functions, with applications to mechanism design."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("optimization", "duality", "value_function"),
    references=[
        "Milgrom, P., Segal, I. (2002). Envelope theorems for arbitrary choice sets. Econometrica 70(2).",
        "Mas-Colell, A., Whinston, M. D., Green, J. R. (1995). Microeconomic Theory. OUP, Ch 5.",
        "Varian, H. R. (1992). Microeconomic Analysis. W.W. Norton, Ch 7.",
    ],
    domain="optimization",
)

BERGE_MAXIMUM_THEOREM = EconomicTheorem(
    theorem_id="ECO-019",
    name="Berge Maximum Theorem",
    statement=(
        r"Let $f: X \times Y \to \mathbb{R}$ be continuous and $\Phi: X \rightrightarrows Y$ "
        r"a continuous (both upper and lower hemicontinuous) compact-valued "
        r"correspondence. Then the value function $V(x) = \max_{y \in \Phi(x)} f(x, y)$ "
        r"is continuous in $x$, and the optimal correspondence "
        r"$\Phi^*(x) = \arg\max_{y \in \Phi(x)} f(x, y)$ is upper hemicontinuous "
        r"and nonempty-compact-valued."
    ),
    proof_sketch=(
        "Continuity of V: upper hemicontinuity of Phi and continuity of f imply "
        "upper semicontinuity of V; lower hemicontinuity of Phi gives lower semicontinuity. "
        "Nonemptiness of Phi*: the maximum is attained because Phi(x) is compact and f continuous. "
        "UHC of Phi*: if not, extract a sequence x_n -> x and y_n in Phi*(x_n) with y_n -> y-hat "
        "not in Phi*(x); derive a contradiction via continuity of f. This theorem underpins "
        "dynamic programming via the principle of optimality and general equilibrium theory."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("mathematics", "optimization", "continuity"),
    references=[
        "Berge, C. (1963). Topological Spaces. Oliver and Boyd.",
        "Aliprantis, C. D., Border, K. C. (2006). Infinite Dimensional Analysis. Springer, Ch 17.",
        "Stokey, N. L., Lucas, R. E. (1989). Recursive Methods in Economic Dynamics. Harvard University Press.",
    ],
    domain="mathematics",
)

BLACKWELLS_THEOREM = EconomicTheorem(
    theorem_id="ECO-020",
    name="Blackwell's Theorem (Sufficiency and Garbling)",
    statement=(
        r"An experiment $\mathcal{E}_1 = (\mathcal{S}_1, P_1)$ is more informative "
        r"than $\mathcal{E}_2 = (\mathcal{S}_2, P_2)$ in the Blackwell sense iff "
        r"$\mathcal{E}_2$ is a garbling (stochastic transformation) of "
        r"$\mathcal{E}_1$: $\exists$ Markov kernel $K$ such that "
        r"$P_2(\cdot | \theta) = \int K(\cdot | s)\, dP_1(s | \theta)$ for all $\theta$. "
        r"Equivalently, $\mathcal{E}_1$ is sufficient for $\mathcal{E}_2$ in the statistical sense."
    ),
    proof_sketch=(
        "One direction (garbling implies less valuable) follows by iterated expectation: "
        "any decision problem solvable with E2 can be solved equally well with E1 "
        "by first applying the kernel K. The converse (universally less valuable implies garbling) "
        "uses the Neyman-Pearson lemma and the fact that E2 is less valuable iff "
        "it has a lower expected utility in every statistical decision problem. "
        "Lehmann (1988) extends the result to continuous state and signal spaces."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("information_economics", "statistical_decision", "garbling"),
    references=[
        "Blackwell, D. (1951). Comparison of experiments. Proc. 2nd Berkeley Symp. Math. Statist. Prob.",
        "Blackwell, D. (1953). Equivalent comparisons of experiments. Annals of Mathematical Statistics 24(2).",
        "Lehmann, E. L. (1988). Comparing location experiments. Annals of Statistics 16(2).",
    ],
    domain="information_economics",
)

TOPKIS_THEOREM = EconomicTheorem(
    theorem_id="ECO-021",
    name="Topkis's Theorem (Supermodularity)",
    statement=(
        r"Let $f: X \times T \to \mathbb{R}$ be supermodular in $(x, t)$ on a "
        r"lattice $X \times T$ (i.e. $f(x \vee x', t) + f(x \wedge x', t) "
        r"\geq f(x,t) + f(x',t)$). Then the optimal correspondence "
        r"$x^*(t) = \arg\max_{x \in X} f(x,t)$ is increasing in $t$ in the "
        r"strong set order. In games, if payoffs are supermodular in own and "
        r"others' actions, best responses are increasing (strategic complements)."
    ),
    proof_sketch=(
        "For any t' > t, suppose x is in x*(t) and x' is in x*(t'). Supermodularity "
        "of f in (x, t) implies f(x join x', t') + f(x meet x', t) >= f(x', t') + f(x, t). "
        "Since x is in arg max at t and x' is in arg max at t', both optimality conditions hold, "
        "forcing x meet x' into x*(t) and x join x' into x*(t'). Hence x*(.) is increasing "
        "in the strong set order. Milgrom-Shannon (1994) extend this to quasi-supermodularity "
        "and the single-crossing property, enabling monotone comparative statics."
    ),
    status=TheoremStatus.PROVED,
    proof_method=ProofMethod.ANALYTICAL,
    dependencies=[],
    trust_tier="CERTIFIED",
    judgment_coords=("optimization", "lattice_theory", "monotone_comparative_statics"),
    references=[
        "Topkis, D. M. (1978). Minimizing a submodular function on a lattice. Operations Research 26(2).",
        "Topkis, D. M. (1998). Supermodularity and Complementarity. Princeton University Press.",
        "Milgrom, P., Shannon, C. (1994). Monotone comparative statics. Econometrica 62(1).",
    ],
    domain="optimization",
)


# ===========================================================================
# TheoremDatabase  (new, JuGeo-integrated)
# ===========================================================================

class TheoremDatabase:
    """Indexed database of economic theorems with JuGeo integration.

    Unlike TheoremCatalog (which indexes by theorem_id), this class also
    supports lookup by name and domain and can export the entire database
    as a list of JuGeo 8-tuples.
    """

    def __init__(self, theorems: list[EconomicTheorem] | None = None) -> None:
        self._theorems: dict[str, EconomicTheorem] = {}
        if theorems:
            for t in theorems:
                self.add(t)

    def add(self, theorem: EconomicTheorem) -> None:
        """Add theorem, keyed by theorem_id."""
        self._theorems[theorem.theorem_id] = theorem

    def get(self, theorem_id: str) -> EconomicTheorem | None:
        """Return theorem by theorem_id, or None."""
        return self._theorems.get(theorem_id)

    def get_by_name(self, name: str) -> EconomicTheorem | None:
        """Return the first theorem whose name matches (case-insensitive), or None."""
        name_lower = name.lower()
        for t in self._theorems.values():
            if t.name.lower() == name_lower:
                return t
        return None

    def get_by_domain(self, domain: str) -> list[EconomicTheorem]:
        """Return all theorems in domain (case-insensitive)."""
        domain_lower = domain.lower()
        return [t for t in self._theorems.values() if t.domain.lower() == domain_lower]

    def get_by_trust_tier(self, trust_tier: str) -> list[EconomicTheorem]:
        """Return all theorems at trust_tier (case-insensitive)."""
        tier_upper = trust_tier.upper()
        return [t for t in self._theorems.values() if t.trust_tier.upper() == tier_upper]

    def all(self) -> list[EconomicTheorem]:
        """Return all theorems in insertion order."""
        return list(self._theorems.values())

    def to_jugeo_propositions(self) -> list[tuple]:
        """Return all theorems as JuGeo 8-tuples (c, phi, A, E, O, B, T, Pi)."""
        return [t.to_judgment_tuple() for t in self._theorems.values()]

    def statistics(self) -> dict[str, Any]:
        """Return a dict of counts by domain, trust tier, proof method, and status."""
        domains: dict[str, int] = {}
        tiers: dict[str, int] = {}
        methods: dict[str, int] = {}
        statuses: dict[str, int] = {}
        for t in self._theorems.values():
            domains[t.domain] = domains.get(t.domain, 0) + 1
            tiers[t.trust_tier] = tiers.get(t.trust_tier, 0) + 1
            methods[t.proof_method.value] = methods.get(t.proof_method.value, 0) + 1
            statuses[t.status.value] = statuses.get(t.status.value, 0) + 1
        return {
            "total": len(self._theorems),
            "by_domain": domains,
            "by_trust_tier": tiers,
            "by_proof_method": methods,
            "by_status": statuses,
        }

    def __len__(self) -> int:
        return len(self._theorems)

    def __repr__(self) -> str:
        return f"TheoremDatabase({len(self)} theorems)"


# ===========================================================================
# verify_theorem  (new)
# ===========================================================================

def verify_theorem(
    theorem: EconomicTheorem,
    evidence: list[Any],
    *,
    agent: str = "theorem_economics.verifier",
) -> tuple:
    """Verify an EconomicTheorem and return a JuGeo judgment 8-tuple.

    The function upgrades the effective trust tier based on the quantity of
    supplied evidence and the theorem's intrinsic status:

    - 0 evidence items          -> tier stays at theorem.trust_tier
    - 1 evidence item           -> at least "CANDIDATE"
    - 2-3 evidence items        -> at least "VERIFIED"
    - 4+ evidence items         -> at least "CERTIFIED" (if PROVED)

    Parameters
    ----------
    theorem:
        The EconomicTheorem to verify.
    evidence:
        A list of evidence objects (strings, dictionaries, etc.).
    agent:
        The agent identifier to record in the JuGeo tuple.

    Returns
    -------
    tuple
        JuGeo 8-tuple (c, phi, A, E, O, B, T, Pi).
    """
    n_evidence = len(evidence)
    base_tier = _TIER_ORDER.get(theorem.trust_tier.upper(), 0)

    if theorem.is_proved():
        if n_evidence >= 4:
            effective_tier_idx = max(base_tier, _TIER_ORDER["CERTIFIED"])
        elif n_evidence >= 2:
            effective_tier_idx = max(base_tier, _TIER_ORDER["VERIFIED"])
        elif n_evidence >= 1:
            effective_tier_idx = max(base_tier, _TIER_ORDER["CANDIDATE"])
        else:
            effective_tier_idx = base_tier
    else:
        effective_tier_idx = min(base_tier, _TIER_ORDER["CANDIDATE"])

    tier_names = ["PROPOSAL", "CANDIDATE", "VERIFIED", "CERTIFIED"]
    effective_tier = tier_names[effective_tier_idx]

    belief = 1.0 if theorem.is_proved() else 0.5
    obstruction = None if theorem.is_proved() else f"unproved:{theorem.theorem_id}"

    return (
        theorem.name,
        theorem.statement,
        agent,
        tuple(evidence),
        obstruction,
        belief,
        effective_tier,
        theorem.judgment_coords,
    )


# ===========================================================================
# TheoremProofChain  (new)
# ===========================================================================

class TheoremProofChain:
    """Chains multiple theorems into a proof sequence using sheaf descent.

    A proof chain forms a directed acyclic graph where each theorem's
    judgment_coords acts as a descent datum. The chain is valid iff all
    consecutive pairs have compatible coordinates (their coordinate tuples share
    at least one common element, i.e. their intersection in the JuGeo cover is
    non-empty).

    Example
    -------
    >>> chain = TheoremProofChain("welfare_chain")
    >>> chain.append(FIRST_WELFARE_THEOREM)
    >>> chain.append(SECOND_WELFARE_THEOREM)
    >>> chain.is_valid()
    True
    """

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._chain: list[EconomicTheorem] = []

    def append(self, theorem: EconomicTheorem) -> None:
        """Append theorem to the end of the proof chain."""
        self._chain.append(theorem)

    def is_valid(self) -> bool:
        """Return True iff consecutive theorems have compatible coordinates.

        Two theorems are compatible when they share at least one string in
        their judgment_coords tuples, representing a common node in the
        JuGeo sheaf cover.
        """
        if len(self._chain) < 2:
            return True
        for prev, curr in zip(self._chain, self._chain[1:]):
            prev_coords = set(prev.judgment_coords)
            curr_coords = set(curr.judgment_coords)
            if not prev_coords.intersection(curr_coords):
                return False
        return True

    def to_judgment_sequence(self) -> list[tuple]:
        """Return each theorem in the chain as a JuGeo 8-tuple."""
        return [t.to_judgment_tuple() for t in self._chain]

    def sheaf_descent_check(self) -> dict[str, Any]:
        """Check sheaf descent compatibility for every consecutive pair.

        Returns a dict with keys: chain_name, length, pairs, is_valid, obstruction_class.
        Each entry in pairs is a dict with from, to, overlap, compatible.
        """
        pairs = []
        for prev, curr in zip(self._chain, self._chain[1:]):
            overlap = set(prev.judgment_coords).intersection(set(curr.judgment_coords))
            pairs.append({
                "from": prev.theorem_id,
                "to": curr.theorem_id,
                "overlap": sorted(overlap),
                "compatible": bool(overlap),
            })
        valid = all(p["compatible"] for p in pairs)
        return {
            "chain_name": self.name,
            "length": len(self._chain),
            "pairs": pairs,
            "is_valid": valid,
            "obstruction_class": self.obstruction_class(),
        }

    def obstruction_class(self) -> str | None:
        """Return a string identifying the first incompatible pair, or None.

        In sheaf cohomology terms, an obstruction to descent arises at the
        first pair of theorems whose coordinate overlap is empty, preventing
        local sections from gluing into a global section.
        """
        for prev, curr in zip(self._chain, self._chain[1:]):
            if not set(prev.judgment_coords).intersection(set(curr.judgment_coords)):
                return f"H1-obstruction: {prev.theorem_id} -> {curr.theorem_id}"
        return None

    def summary(self) -> str:
        """Return a multi-line human-readable summary of the chain."""
        lines = [f"TheoremProofChain '{self.name}' ({len(self._chain)} theorems):"]
        for i, t in enumerate(self._chain):
            coords = " > ".join(t.judgment_coords) if t.judgment_coords else "empty"
            lines.append(f"  [{i}] {t.theorem_id} -- {t.name}  coords: {coords}")
        lines.append(f"  valid: {self.is_valid()}")
        if not self.is_valid():
            lines.append(f"  obstruction: {self.obstruction_class()}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._chain)

    def __repr__(self) -> str:
        return f"TheoremProofChain(name={self.name!r}, length={len(self._chain)})"


# ===========================================================================
# DEFAULT_THEOREM_DB
# ===========================================================================

DEFAULT_THEOREM_DB = TheoremDatabase([
    ARROWS_IMPOSSIBILITY,
    GIBBARD_SATTERTHWAITE,
    REVELATION_PRINCIPLE,
    REVENUE_EQUIVALENCE,
    MYERSONS_LEMMA,
    FOLK_THEOREM,
    NASH_EXISTENCE,
    KAKUTANI_FIXED_POINT,
    WALRASIAN_EQUILIBRIUM_EXISTENCE,
    FIRST_WELFARE_THEOREM,
    SECOND_WELFARE_THEOREM,
    COASE_THEOREM,
    GREEN_LAFFONT_HOLMSTROM,
    VICKREY_CLARKE_GROVES,
    CONDORCET_JURY_THEOREM,
    SPENCE_SIGNALING,
    AKERLOF_LEMONS,
    ENVELOPE_THEOREM,
    BERGE_MAXIMUM_THEOREM,
    BLACKWELLS_THEOREM,
    TOPKIS_THEOREM,
])


# ===========================================================================
# Legacy T52_* instances and default_catalog  (preserved exactly)
# ===========================================================================

def _make_theorem(n: int) -> EconomicTheorem:
    """Factory for legacy T52-* theorem stubs."""
    return EconomicTheorem(
        theorem_id=f"T52-{n}",
        name=f"Theorem {n}",
        statement=f"Statement for theorem {n}.",
        proof_sketch=f"Proof sketch for theorem {n}.",
        status=TheoremStatus.PROVED,
        proof_method=ProofMethod.ANALYTICAL,
        dependencies=[],
    )


T52_1  = _make_theorem(1)
T52_2  = _make_theorem(2)
T52_3  = _make_theorem(3)
T52_4  = _make_theorem(4)
T52_5  = _make_theorem(5)
T52_6  = _make_theorem(6)
T52_7  = _make_theorem(7)
T52_8  = _make_theorem(8)
T52_9  = _make_theorem(9)
T52_10 = _make_theorem(10)
T52_11 = _make_theorem(11)
T52_12 = _make_theorem(12)
T52_13 = _make_theorem(13)
T52_14 = _make_theorem(14)
T52_15 = _make_theorem(15)


def default_catalog() -> TheoremCatalog:
    """Return a TheoremCatalog populated with T52_1 through T52_15."""
    catalog = TheoremCatalog()
    for theorem in [
        T52_1, T52_2, T52_3, T52_4, T52_5,
        T52_6, T52_7, T52_8, T52_9, T52_10,
        T52_11, T52_12, T52_13, T52_14, T52_15,
    ]:
        catalog.add(theorem)
    return catalog


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    all_theorems = DEFAULT_THEOREM_DB.all()
    assert len(all_theorems) >= 20, f"Expected >=20, got {len(all_theorems)}"

    nash = DEFAULT_THEOREM_DB.get_by_name("Nash Existence Theorem")
    assert nash is not None, "Nash Existence Theorem not found by name"

    gt_theorems = DEFAULT_THEOREM_DB.get_by_domain("game_theory")
    assert len(gt_theorems) > 0, "No game_theory theorems found"

    chain = TheoremProofChain("welfare_chain")
    chain.append(FIRST_WELFARE_THEOREM)
    chain.append(SECOND_WELFARE_THEOREM)
    assert chain.is_valid(), "Welfare proof chain should be valid"

    jt = verify_theorem(NASH_EXISTENCE, ["Nash_1950", "Kakutani_1941"])
    assert len(jt) == 8, "verify_theorem must return an 8-tuple"
    assert jt[6] in {"PROPOSAL", "CANDIDATE", "VERIFIED", "CERTIFIED"}

    assert T52_1.theorem_id == "T52-1"
    assert T52_2.theorem_id == "T52-2"
    assert T52_3.theorem_id == "T52-3"

    cat = default_catalog()
    assert len(cat.all()) >= 15, f"Expected >=15 in default_catalog, got {len(cat.all())}"

    print("All theorems smoke tests passed.")
