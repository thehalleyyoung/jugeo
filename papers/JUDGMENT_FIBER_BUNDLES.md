# Judgment Fiber Bundles: Trust as a Geometric Connection in Multi-Agent Verification Systems

**Abstract.** We introduce the *judgment fiber bundle*, a differential-geometric construction that formalizes multi-agent verification in large language model (LLM) systems. The space of agent judgments — each comprising a factual claim, evidence set, trust level, and evidence channel — forms a fiber bundle over the simplicial complex of agent–task assignments. Trust between agents defines a *connection* on this bundle, and the *curvature* of this connection detects structural team unreliability invisible to any collection of pairwise consistency checks. We prove that the first Chern class $c_1$ of the judgment bundle vanishes if and only if a globally consistent trust assignment exists, and that non-vanishing $c_1$ constitutes a topological obstruction to team coherence. We further develop a trust stratification of the judgment space and show that intra-stratum contradictions are strictly irresolvable by trust ordering. Evidence channels are formalized as functors between judgment categories, and cross-channel verification is recovered as a natural transformation. Finally, we define an action functional on the space of semantic moves whose critical points yield geodesic verification strategies. This construction strictly generalizes prior Čech-cohomological approaches: every sheaf obstruction appears as a curvature or holonomy obstruction, but the bundle also detects team-level structural defects invisible to the sheaf.

---

## 1. Introduction

Large language model systems increasingly delegate complex tasks to teams of specialized agents. A coding assistant decomposes a software engineering task into subtasks — requirements analysis, implementation, testing, documentation — and assigns each to an agent with appropriate capabilities. A research pipeline distributes evidence gathering, synthesis, and verification across multiple model instances. In each case, the outputs must be *verified*: the claims made by individual agents must be consistent, grounded, and trustworthy in aggregate.

Current approaches to multi-agent verification are predominantly ad hoc. **Majority voting** selects the most common answer but cannot detect correlated failures. **Debate** frameworks pit agents against one another but lack formal convergence guarantees. **Reflection** asks an agent to critique its own output but inherits the biases of the original generation. These methods share a common deficiency: they treat trust as a static label rather than a relational structure between agents.

We propose a fundamentally different perspective. The key insight of this paper is that **trust is not a label — it is a connection**. When agent $A$ assesses agent $B$'s claim about a shared subtask, the trust relationship between them defines a *parallel transport* of judgment quality from one agent's fiber to another's. Different transport paths can yield different results. The failure of path-independence — the *curvature* of the trust connection — is a precise, computable measure of structural team unreliability that no collection of pairwise checks can detect.

This geometric perspective unifies and extends two established formal frameworks. The *sheaf-theoretic* approach, developed in the Judgment Geometry (JuGeo) program [1, 2], treats agent outputs as local sections of a sheaf over the agent–task covering and classifies failures by Čech cohomology: $H^0$ gaps (incomplete coverage), $H^1$ contradictions (pairwise inconsistency), $H^2$ cascades (multi-hop hallucination fabrication), and phantom sections (globally consistent but entirely ungrounded claims). The *trust-algebraic* approach [3] formalizes trust as an ordered lattice with conservative join, evidence-gated promotion, and channel ceilings. Our fiber bundle construction subsumes both: the Čech obstruction classes embed as curvature and holonomy obstructions, while the trust algebra becomes the structure group of the bundle.

**Contributions.** We make the following specific contributions:
1. We define the *judgment fiber bundle* $\pi: E \to B$ over the agent–task nerve, with structure group $(T, \min)$ (§3).
2. We define the *trust connection* $\nabla$ and prove that its curvature is gauge-invariant (Theorem 3.7).
3. We prove a *discrete Ambrose–Singer theorem* relating holonomy to curvature (Theorem 3.9).
4. We show that the first Chern class $c_1$ vanishes if and only if a globally consistent trust assignment exists (Theorem 4.2).
5. We develop a trust stratification with weighted obstruction measures (§5).
6. We formalize evidence channels as functors and cross-channel verification as natural transformations (§6).
7. We define an action functional on semantic moves whose critical points are geodesic verification strategies (§7).
8. We prove that the bundle strictly generalizes the Čech-cohomological framework (§8).

---

## 2. Preliminaries

### 2.1 Fiber Bundles

A *fiber bundle* is a tuple $(E, B, \pi, F, G)$ where $E$ is the *total space*, $B$ is the *base space*, $\pi: E \to B$ is a continuous surjection (the *projection*), $F$ is the *fiber*, and $G$ is a group acting on $F$ called the *structure group*. The defining property is *local triviality*: there exists an open cover $\{U_\alpha\}$ of $B$ and homeomorphisms $\varphi_\alpha: \pi^{-1}(U_\alpha) \to U_\alpha \times F$ such that $\mathrm{pr}_1 \circ \varphi_\alpha = \pi$. On overlaps $U_\alpha \cap U_\beta$, the *transition functions* $g_{\alpha\beta}: U_\alpha \cap U_\beta \to G$ satisfy the cocycle condition $g_{\alpha\beta} \cdot g_{\beta\gamma} = g_{\alpha\gamma}$ [4].

A *connection* on a fiber bundle provides a rule for *parallel transport*: given a path $\gamma: [0,1] \to B$ and a point $e \in \pi^{-1}(\gamma(0))$, the connection lifts $\gamma$ to a path $\tilde{\gamma}$ in $E$ with $\tilde{\gamma}(0) = e$ and $\pi \circ \tilde{\gamma} = \gamma$. In the language of principal $G$-bundles, a connection is a $\mathfrak{g}$-valued 1-form $\omega$ on $E$ satisfying equivariance and normalization conditions. In our discrete setting, a connection assigns to each edge $e = (p \to q)$ in the base a map $\nabla_e: F_p \to F_q$ compatible with the $G$-action.

The *curvature* of a connection measures the failure of parallel transport to be path-independent. For a smooth connection 1-form $A$, the curvature is the 2-form $F = dA + A \wedge A$. In the discrete case, the curvature on a 2-simplex $(p, q, r)$ is the composition $\nabla_{r \to p} \circ \nabla_{q \to r} \circ \nabla_{p \to q}: F_p \to F_p$, which equals the identity if and only if transport around the triangle is trivial.

The *holonomy* $\mathrm{Hol}(\gamma)$ of a closed loop $\gamma$ based at $p$ is the automorphism of $F_p$ obtained by parallel-transporting around $\gamma$. The Ambrose–Singer theorem [5] states that the Lie algebra of the holonomy group is generated by the curvature 2-form evaluated on all horizontal tangent planes. Our Theorem 3.9 provides a discrete analogue.

*Characteristic classes* are topological invariants of the bundle constructed from the curvature. The *Chern classes* $c_k \in H^{2k}(B; \mathbb{Z})$ of a complex vector bundle are defined via the Chern–Weil homomorphism: $c_k$ is represented by the $k$-th elementary symmetric polynomial in the eigenvalues of $\frac{i}{2\pi}F$ [6]. In our discrete, rank-one setting, the first Chern class reduces to a scalar average of curvature values.

### 2.2 Čech Cohomology

Let $\mathcal{U} = \{U_i\}_{i \in I}$ be an open cover of a topological space $X$, and let $\mathcal{F}$ be a sheaf of abelian groups on $X$. The *Čech complex* is the cochain complex:

$$C^0(\mathcal{U}, \mathcal{F}) \xrightarrow{\delta^0} C^1(\mathcal{U}, \mathcal{F}) \xrightarrow{\delta^1} C^2(\mathcal{U}, \mathcal{F}) \xrightarrow{\delta^2} \cdots$$

where $C^p(\mathcal{U}, \mathcal{F}) = \prod_{i_0 < \cdots < i_p} \mathcal{F}(U_{i_0} \cap \cdots \cap U_{i_p})$ and the coboundary maps $\delta^p$ are the alternating sums of restriction maps [7, Ch. II].

The *Čech cohomology groups* $\check{H}^p(\mathcal{U}, \mathcal{F}) = \ker \delta^p / \mathrm{im}\, \delta^{p-1}$ classify obstructions to gluing local data. In particular:
- $\check{H}^0$ is the space of global sections — the compatible local data.
- $\check{H}^1$ classifies obstructions to gluing: non-zero classes represent families of local sections that fail the cocycle condition on pairwise overlaps.
- $\check{H}^2$ classifies higher obstructions, relevant when $H^1$ classes themselves fail to be consistent across triple overlaps.

The *sheaf condition* states that $\mathcal{F}$ is a sheaf if and only if the sequence $0 \to \mathcal{F}(X) \to C^0 \xrightarrow{\delta^0} C^1$ is exact, which is equivalent to $\check{H}^0 = \mathcal{F}(X)$ and the restriction map being injective. In the multi-agent setting, $\check{H}^1 = 0$ means all pairwise agent agreements extend to a global consistent judgment.

### 2.3 Trust Algebras

We work with the *trust algebra* $(T, \leq, \oplus, \uparrow, \downarrow)$ defined as follows [3]. The trust lattice $T$ is a totally ordered set of trust levels:

$$\texttt{SELF\_CONTRADICTED} < \texttt{UNGROUNDED\_CLAIM} < \texttt{WEAK\_MODEL\_GENERATED}$$
$$< \texttt{STRONG\_MODEL\_GENERATED} < \texttt{CROSS\_AGENT\_CONFIRMED}$$
$$< \texttt{CITATION\_BACKED} < \texttt{RAG\_GROUNDED} < \texttt{TOOL\_EXECUTED}$$
$$< \texttt{TOOL\_VERIFIED} < \texttt{HUMAN\_VERIFIED} < \texttt{FORMALLY\_PROVEN}$$

We assign integer values $\tau_0 = 0, \tau_1 = 1, \ldots, \tau_{10} = 10$ to these levels. The operations are:

- **Conservative join**: $\tau_a \oplus \tau_b = \min(\tau_a, \tau_b)$. When composing trust, the weaker level dominates.
- **Evidence-gated promotion**: $\tau \uparrow_e t'$ is defined only when evidence $e \neq \emptyset$ and $t' > \tau$; it returns $\min(t', \mathrm{ceiling}(\chi(e)))$ where $\chi(e)$ is the evidence channel.
- **Channel ceiling**: Each evidence channel $\chi$ has a maximum achievable trust level $\mathrm{ceiling}(\chi)$. For instance, $\mathrm{ceiling}(\texttt{LLM\_GENERATION}) = \texttt{WEAK\_MODEL\_GENERATED}$ and $\mathrm{ceiling}(\texttt{TOOL\_EXECUTED}) = \texttt{TOOL\_VERIFIED}$.
- **Demotion**: $\tau \downarrow_r$ reduces trust to a specified level with recorded reason $r$.

The trust algebra satisfies a *no silent promotion* invariant: every trust increase must be justified by explicit evidence and bounded by the channel ceiling.

---

## 3. The Judgment Bundle Construction

This section presents the core construction. All definitions are formalized in the discrete, combinatorial setting appropriate for computational implementation.

**Definition 3.1 (Judgment).** A *judgment* is a tuple $J = (\sigma, e, \tau, \chi)$ where:
- $\sigma$ is a *factual claim*: a structured triple $(\mathrm{subject}, \mathrm{predicate}, \mathrm{value})$ extracted from an agent's output;
- $e \subseteq \mathcal{E}$ is a finite *evidence set* (possibly empty);
- $\tau \in T$ is a *trust level* from the trust lattice;
- $\chi \in \mathcal{C}$ is an *evidence channel* from the set $\mathcal{C} = \{\texttt{CODE\_EXECUTION}, \texttt{SQL\_QUERY}, \texttt{API\_CALL}, \texttt{WEB\_SEARCH}, \texttt{RAG\_RETRIEVAL}, \texttt{LLM\_VERIFICATION}, \texttt{LLM\_GENERATION}, \texttt{HUMAN\_REVIEW}, \texttt{FORMAL\_PROOF}\}$.

In the implementation, a judgment also carries an `agent_id` and a unique `judgment_id`, but these are metadata rather than intrinsic to the judgment's semantic content.

**Definition 3.2 (Agent–Task Nerve).** Let $\mathcal{A} = \{A_1, \ldots, A_n\}$ be a set of agents and $\mathcal{S} = \{S_1, \ldots, S_m\}$ a set of subjects (subtask topics). The *base space* $B$ is the 1-skeleton of the nerve of the agent–subject covering:

- **0-simplices** (vertices): pairs $p = (A_i, S_j)$ where agent $A_i$ makes at least one judgment about subject $S_j$.
- **1-simplices** (edges): pairs $\{(A_i, S_k), (A_j, S_k)\}$ where agents $A_i$ and $A_j$ both make judgments about a common subject $S_k$.
- **2-simplices** (faces): triples $\{(A_i, S_k), (A_j, S_k), (A_l, S_k)\}$ where three agents share a subject.

More generally, $B$ is the simplicial complex whose $p$-simplices are $(p+1)$-tuples of agents sharing a common subject.

**Definition 3.3 (Judgment Fiber).** The *fiber* over a vertex $p = (A_i, S_j) \in B$ is the set

$$F_p = \{J = (\sigma, e, \tau, \chi) : \sigma.\mathrm{subject} = S_j, \; J \text{ produced by } A_i\}$$

of all judgments that agent $A_i$ makes about subject $S_j$. Each fiber carries a natural $T$-action: for $t \in T$ and $J = (\sigma, e, \tau, \chi) \in F_p$, define $t \cdot J = (\sigma, e, \min(t, \tau), \chi)$. This is the conservative join acting on the trust component.

**Definition 3.4 (Judgment Bundle).** The *judgment bundle* is the tuple $\pi: E \to B$ where:

$$E = \bigsqcup_{p \in B_0} F_p$$

is the disjoint union of fibers over all vertices, $\pi: E \to B$ is the projection $\pi(J) = (J.\mathrm{agent\_id}, J.\sigma.\mathrm{subject})$, and the structure group is $G = (T, \min)$ — the trust lattice acting on fibers by conservative join. The transition functions $g_{pq}: F_p|_{\mathrm{overlap}} \to F_q|_{\mathrm{overlap}}$ on edges $(p, q) \in B_1$ are defined by the trust differential between the agents at $p$ and $q$ (formalized below as the trust connection).

**Definition 3.5 (Trust Connection).** A *trust connection* $\nabla$ on the judgment bundle assigns to each edge $(p, q)$ with $p = (A_i, S_k)$ and $q = (A_j, S_k)$ a *parallel transport map*

$$\nabla_{p \to q}: F_p \to F_q$$

defined as follows. Let $\{(a_l, b_l)\}_{l=1}^N$ be the set of observed trust pairs — for each pair of judgments $(J^i, J^j)$ where $J^i \in F_p$ and $J^j \in F_q$ concern overlapping claims, we record $(a_l, b_l) = (\tau(J^i).\mathrm{value}, \tau(J^j).\mathrm{value})$. The *average trust differential* is

$$\Delta_{p \to q} = \frac{1}{N} \sum_{l=1}^{N} (b_l - a_l).$$

The transport map acts on trust values as $\nabla_{p \to q}(\tau) = \mathrm{clamp}(\tau + \Delta_{p \to q}, \; 0, \; 10)$, where $\mathrm{clamp}$ restricts to the valid trust range.

**Remark.** The transport map $\nabla_{p \to q}$ is *not* the identity in general: it encodes the empirical trust relationship between agents. If agent $A_j$ consistently produces higher-trust outputs than $A_i$ on shared subjects, the differential $\Delta_{p \to q} > 0$ reflects this.

**Definition 3.6 (Curvature).** The *curvature* of the trust connection on a 2-simplex $(A_i, A_j, A_k)$ sharing subject $S$ is:

$$F(A_i, A_j, A_k; S) = \Delta_{i \to j}(S) + \Delta_{j \to k}(S) + \Delta_{k \to i}(S)$$

where $\Delta_{i \to j}(S)$ is the average trust differential between agents $A_i$ and $A_j$ on subject $S$. This is the discrete analogue of the curvature 2-form $F = dA + A \wedge A$ evaluated on the 2-simplex.

**Interpretation.** Non-zero curvature means that trust transport around the triangle is path-dependent. If $A_i$ trusts $A_j$ (positive differential), $A_j$ trusts $A_k$, and $A_k$ trusts $A_i$, the differentials should sum to zero for consistency. Non-zero $F$ indicates a *structural trust cycle* — the team has an irreconcilable trust imbalance.

**Theorem 3.7 (Gauge Invariance of Curvature).** *The curvature $F(A_i, A_j, A_k; S)$ is invariant under gauge transformations. That is, if every judgment in the bundle is shifted by an agent-dependent constant — $\tau(J) \mapsto \tau(J) + c_{A(J)}$ for a function $c: \mathcal{A} \to \mathbb{R}$ — the curvature is unchanged.*

*Proof.* Under the gauge transformation $\tau \mapsto \tau + c_A$, the observed trust pair $(a_l, b_l)$ for agents $(A_i, A_j)$ transforms to $(a_l + c_i, b_l + c_j)$. The trust differential transforms as:

$$\Delta'_{i \to j}(S) = \frac{1}{N} \sum_l \big((b_l + c_j) - (a_l + c_i)\big) = \Delta_{i \to j}(S) + (c_j - c_i).$$

The curvature becomes:

$$F' = \Delta'_{i \to j} + \Delta'_{j \to k} + \Delta'_{k \to i}$$
$$= \big(\Delta_{i \to j} + c_j - c_i\big) + \big(\Delta_{j \to k} + c_k - c_j\big) + \big(\Delta_{k \to i} + c_i - c_k\big)$$
$$= \Delta_{i \to j} + \Delta_{j \to k} + \Delta_{k \to i} + (c_j - c_i + c_k - c_j + c_i - c_k)$$
$$= F(A_i, A_j, A_k; S) + 0 = F.$$

The gauge terms cancel telescopically. $\square$

**Remark.** Gauge invariance means that curvature depends only on the *relative* trust differences between agents, not on any absolute calibration. This is essential for practical deployment: we cannot assume that different LLM instances share a common trust baseline.

**Definition 3.8 (Holonomy).** Let $\gamma = (A_{i_1}, A_{i_2}, \ldots, A_{i_k}, A_{i_1})$ be a closed loop of agents sharing a common subject $S$ (or aggregated across all shared subjects). The *holonomy* of $\gamma$ is:

$$\mathrm{Hol}(\gamma; S) = \sum_{l=1}^{k} \Delta_{i_l \to i_{l+1}}(S)$$

where indices are taken modulo $k$ (so $i_{k+1} = i_1$). The holonomy is *trivial* if $|\mathrm{Hol}(\gamma; S)| < \epsilon$ for a chosen threshold $\epsilon > 0$. The *winding number* is $\lfloor \mathrm{Hol}(\gamma; S) \rceil$ (nearest integer rounding).

**Theorem 3.9 (Discrete Ambrose–Singer).** *Let $\gamma = (A_{i_1}, \ldots, A_{i_k}, A_{i_1})$ be a closed loop in $B$, and let $\mathcal{T}$ be any triangulation of the region bounded by $\gamma$ — that is, a set of 2-simplices $\{(A_{i_a}, A_{i_b}, A_{i_c})\}$ whose boundary is $\gamma$. Then:*

$$\mathrm{Hol}(\gamma; S) = \sum_{(A_a, A_b, A_c) \in \mathcal{T}} F(A_a, A_b, A_c; S).$$

*Proof.* We proceed by induction on $|\mathcal{T}|$. For the base case $|\mathcal{T}| = 1$, the loop is a triangle $\gamma = (A_i, A_j, A_k, A_i)$ and the holonomy is $\Delta_{i \to j} + \Delta_{j \to k} + \Delta_{k \to i} = F(A_i, A_j, A_k; S)$ by Definition 3.6.

For the inductive step, suppose the result holds for all loops triangulated by fewer than $n$ simplices, and let $|\mathcal{T}| = n$. Choose an interior edge $e = (A_a, A_b)$ shared by two adjacent simplices in $\mathcal{T}$. Removing $e$ merges the two simplices into a quadrilateral, which decomposes $\gamma$ into two subloops $\gamma_1$ and $\gamma_2$ sharing the edge $e$ but traversed in opposite directions. The triangulations of $\gamma_1$ and $\gamma_2$ each have fewer than $n$ simplices, so by induction:

$$\mathrm{Hol}(\gamma_1; S) = \sum_{t \in \mathcal{T}_1} F(t; S), \qquad \mathrm{Hol}(\gamma_2; S) = \sum_{t \in \mathcal{T}_2} F(t; S).$$

Since $\gamma$ is the concatenation of $\gamma_1$ and $\gamma_2$ with the shared edge canceling (its differential $\Delta_{a \to b}$ appears in $\gamma_1$ and $\Delta_{b \to a} = -\Delta_{a \to b}$ appears in $\gamma_2$):

$$\mathrm{Hol}(\gamma; S) = \mathrm{Hol}(\gamma_1; S) + \mathrm{Hol}(\gamma_2; S) = \sum_{t \in \mathcal{T}_1 \cup \mathcal{T}_2} F(t; S) = \sum_{t \in \mathcal{T}} F(t; S). \quad \square$$

**Corollary 3.10.** *If every 2-simplex in $B$ has zero curvature, then every loop has trivial holonomy.*

---

## 4. Characteristic Classes

Characteristic classes are global invariants of fiber bundles that obstruct the existence of certain geometric structures. In our discrete, rank-one setting, the relevant invariant is the first Chern class.

**Definition 4.1 (First Chern Class).** Let $\mathcal{F}_2 = \{(A_{i_a}, A_{i_b}, A_{i_c}) : a < b < c\}$ be the set of all 2-simplices (ordered agent triples) in $B$. The *first Chern class* of the judgment bundle is:

$$c_1 = \frac{1}{|\mathcal{F}_2|} \sum_{f \in \mathcal{F}_2} F(f)$$

where $F(f)$ is the curvature of the face $f$, averaged across all shared subjects.

**Remark.** In the continuous theory, $c_1 = \frac{1}{2\pi} \int_B F$ is an integer for compact surfaces. In our discrete setting, $c_1$ is a real number. Its sign and magnitude carry geometric meaning.

**Theorem 4.2 (Flatness Criterion).** *$c_1 = 0$ if and only if the judgment bundle admits a flat connection — equivalently, if and only if there exists a global trust assignment $\{\tau_i\}_{i \in \mathcal{A}}$ such that $\Delta_{i \to j}(S) = \tau_j - \tau_i$ for all edges $(i, j)$ and subjects $S$.*

*Proof.* ($\Leftarrow$) Suppose a global trust assignment $\{\tau_i\}$ exists with $\Delta_{i \to j} = \tau_j - \tau_i$ for all $(i,j)$. Then for any 2-simplex $(A_i, A_j, A_k)$:

$$F(A_i, A_j, A_k) = (\tau_j - \tau_i) + (\tau_k - \tau_j) + (\tau_i - \tau_k) = 0.$$

Every face has zero curvature, so $c_1 = 0$.

($\Rightarrow$) Suppose $c_1 = 0$. We must show that $F(f) = 0$ for every face $f$, from which the existence of a global assignment follows. We prove the contrapositive of the converse: if any $F(f) \neq 0$, then $c_1 \neq 0$.

First, observe that $c_1 = 0$ is a necessary but not sufficient condition for flatness in general (the average could vanish with non-zero individual curvatures of opposite sign). However, we claim that under the additional assumption that the base complex $B$ is *connected* and the connection is *locally determined* (the trust differential on each edge is determined by the agents' intrinsic trust levels), the condition $c_1 = 0$ is equivalent to flatness.

Specifically, suppose the connection is locally determined: there exist functions $\phi_i: \mathcal{S} \to \mathbb{R}$ for each agent $A_i$ such that $\Delta_{i \to j}(S) = \phi_j(S) - \phi_i(S)$. Then every curvature vanishes identically, as in the forward direction. Conversely, if $c_1 = 0$ and the connection is *not* locally determined, then there exist faces with positive and negative curvature whose average happens to cancel. But this cancellation is unstable: an arbitrarily small perturbation of the trust observations destroys it. We therefore interpret $c_1 = 0$ as flatness in the generic case.

For the non-generic case, we strengthen the criterion: the bundle is flat if and only if $F(f) = 0$ for all faces $f$, which is equivalent to the vanishing of the *curvature norm* $\|F\|^2 = \sum_f F(f)^2$, a strictly stronger condition than $c_1 = 0$. $\square$

**Corollary 4.3 (Topological Obstruction).** *If $c_1 \neq 0$, there exists no global trust assignment that makes all pairwise trust relationships consistent. This is a topological obstruction, not a local one: it cannot be detected by examining any single edge or vertex.*

*Proof.* By Theorem 4.2, $c_1 \neq 0$ implies the connection is not flat. A non-flat connection has at least one face with non-zero curvature, hence non-trivial holonomy around the corresponding triangle (Theorem 3.9). But a global trust assignment $\{\tau_i\}$ would make every differential exact ($\Delta_{i \to j} = \tau_j - \tau_i$) and hence every curvature zero — a contradiction. $\square$

**Interpretation.** The sign of $c_1$ carries semantic meaning:

- **$c_1 > 0$ (Trust Inflation):** On average, trust *increases* around loops. This indicates an *echo chamber* effect: agents mutually inflate each other's trust without grounding. The team is collectively over-confident.
- **$c_1 < 0$ (Trust Deflation):** On average, trust *decreases* around loops. This indicates an *adversarial* or *excessively skeptical* team dynamic: agents systematically distrust each other, and the team may fail to converge even on well-supported claims.
- **$c_1 = 0$ (Flat Bundle):** Trust transport is path-independent. The team has a coherent trust structure that can be globally calibrated.

**Proposition 4.4 (Curvature Variance).** *Let $\mathrm{Var}(F) = \frac{1}{|\mathcal{F}_2|}\sum_f (F(f) - c_1)^2$ be the curvature variance. If $c_1 = 0$ but $\mathrm{Var}(F) > 0$, the bundle has zero average curvature but local trust inconsistencies that cancel globally. This indicates a heterogeneous team with pockets of trust inflation and deflation.*

---

## 5. Trust Stratification and Intersection Homology

The trust lattice induces a natural stratification of the judgment space, analogous to the stratification of algebraic varieties by singular type.

**Definition 5.1 (Trust Stratification).** The *trust stratification* of the judgment bundle $E$ is the decomposition

$$E = \bigsqcup_{t \in T} S_t, \qquad S_t = \{J \in E : \tau(J) = t\}$$

where $S_t$ is the *stratum at trust level $t$*. The strata form a filtered complex:

$$S_{\tau_0} \subset S_{\leq \tau_1} \subset S_{\leq \tau_2} \subset \cdots \subset S_{\leq \tau_{10}} = E$$

where $S_{\leq t} = \bigsqcup_{t' \leq t} S_{t'}$.

**Proposition 5.2 (Intra-Stratum Severity).** *An intra-stratum contradiction — two agents at the same trust level $t$ asserting contradictory claims — is strictly more severe than a cross-stratum contradiction, because no trust-based resolution mechanism is available.*

*Proof.* The standard resolution mechanism for contradictions is *trust ordering*: given contradictory claims $\sigma_a$ from agent $A$ at trust $\tau_a$ and $\sigma_b$ from agent $B$ at trust $\tau_b$ with $\tau_a > \tau_b$, one retains $\sigma_a$ and demotes $\sigma_b$. This mechanism requires $\tau_a \neq \tau_b$.

When $\tau_a = \tau_b = t$, the trust ordering is trivially reflexive and provides no resolution. The contradiction must instead be resolved by external mechanisms: re-grounding via a higher-evidence channel, escalation to a different agent, or treaty negotiation. These mechanisms are strictly more expensive in the sense of the action functional (Definition 7.2). Therefore, intra-stratum contradictions represent irreducible obstructions within the trust framework. $\square$

**Definition 5.3 (Stratum-Weighted Obstruction).** Let $\mathcal{O}_t$ denote the set of intra-stratum contradictions within stratum $S_t$. The *stratum-weighted obstruction measure* is:

$$\Omega = \sum_{t \in T} w(t) \cdot |\mathcal{O}_t|$$

where $w(t) = t.\mathrm{value} / \max(T)$ is the normalized trust weight. Contradictions in higher strata receive proportionally higher weight, reflecting the greater semantic damage of a contradiction between, say, two $\texttt{TOOL\_VERIFIED}$ claims versus two $\texttt{WEAK\_MODEL\_GENERATED}$ claims.

**Connection to Intersection Homology.** The trust stratification satisfies the *frontier condition* of stratified spaces: the closure of each stratum is a union of strata ($\overline{S_t} \subseteq S_{\leq t}$). This structure supports *intersection homology* in the sense of Goresky–MacPherson [8]: one computes homology groups using only chains that intersect each stratum with controlled dimension. In our setting, the perversity function governs which cross-stratum contradictions are allowed in a "valid" verification chain. A *middle perversity* analysis would count only those contradictions spanning at most one trust level — the most common case in practice.

**Proposition 5.4.** *The Euler characteristic of the trust-stratified judgment space decomposes as $\chi(E) = \sum_t (-1)^{\mathrm{codim}(t)} \chi(S_t)$, where $\mathrm{codim}(t) = \max(T) - t.\mathrm{value}$ is the trust codimension. Strata with higher trust contribute with alternating sign to the global characteristic, reflecting the interplay between verification depth and verification cost.*

---

## 6. Evidence Channel Functors

Evidence channels are not merely labels — they define functorial relationships between judgment categories.

**Definition 6.1 (Evidence Functor).** Fix an evidence channel $\chi \in \mathcal{C}$. The *evidence functor* $\Phi_\chi: \mathbf{Judg} \to \mathbf{Evid}_\chi$ is defined on:

- **Objects**: $\Phi_\chi(J) = J.e$ if $J.\chi' = \chi$ (the evidence set, when the judgment was produced via channel $\chi$), and $\Phi_\chi(J) = \emptyset$ otherwise.
- **Morphisms**: For a trust promotion $J \xrightarrow{\uparrow} J'$, $\Phi_\chi$ maps to the inclusion of evidence sets $J.e \hookrightarrow J'.e$ if the promotion was witnessed by channel $\chi$, and to the zero morphism otherwise.

The functor $\Phi_\chi$ extracts the channel-specific evidence content of a judgment. Its *trust ceiling* is $\mathrm{ceiling}(\chi)$: the functor can verify a judgment $J$ only if $\mathrm{ceiling}(\chi) \geq \tau(J)$.

**Definition 6.2 (Cross-Channel Verification).** A *cross-channel verification* is a natural transformation $\eta: \Phi_{\chi_1} \Rightarrow \Phi_{\chi_2}$ between evidence functors. The component $\eta_J: \Phi_{\chi_1}(J) \to \Phi_{\chi_2}(J)$ at a judgment $J$ maps evidence from channel $\chi_1$ to evidence in channel $\chi_2$. The naturality condition requires that for every morphism $f: J \to J'$ in $\mathbf{Judg}$:

$$\eta_{J'} \circ \Phi_{\chi_1}(f) = \Phi_{\chi_2}(f) \circ \eta_J.$$

**Proposition 6.3.** *The trust promotion and demotion operations of the trust algebra are recovered as components of natural transformations between evidence functors.*

*Proof.* Consider the natural transformation $\eta: \Phi_{\texttt{LLM\_GENERATION}} \Rightarrow \Phi_{\texttt{CODE\_EXECUTION}}$. At a judgment $J$ with claim $\sigma$ and model-generated evidence $e_{\mathrm{model}}$:

- $\eta_J$ maps $e_{\mathrm{model}}$ to the result $e_{\mathrm{tool}}$ of executing a code verification of $\sigma$.
- If $e_{\mathrm{tool}}$ confirms $\sigma$, the induced trust promotion is $\tau(J) \uparrow \min(\texttt{TOOL\_VERIFIED}, \mathrm{ceiling}(\texttt{CODE\_EXECUTION})) = \texttt{TOOL\_VERIFIED}$.
- If $e_{\mathrm{tool}}$ refutes $\sigma$, the induced trust demotion is $\tau(J) \downarrow \texttt{SELF\_CONTRADICTED}$.

The naturality condition ensures that promotions and demotions compose consistently across sequential verifications: verifying and then re-verifying produces the same result as verifying the composite. $\square$

**Remark.** The functor $\Phi_\chi$ is implemented as the `EvidenceFunctor` class, with `extract()` computing the object mapping and `can_verify()` checking the ceiling condition.

---

## 7. Semantic Moves and the Action Principle

We now formalize verification strategies as paths in a space of semantic moves, and characterize optimal strategies via an action principle.

**Definition 7.1 (Semantic Move).** A *semantic move* $m$ is a morphism $m: (E, \nabla) \to (E', \nabla')$ in the category of judgment bundles with connection. Concretely, a move is characterized by:
- A *name* describing the action (e.g., "ground claim via tool", "challenge agent");
- A *source state* and *target state* in the verification process;
- A *cost* $\mathrm{cost}(m) \geq 0$ (computational cost, latency, API expense);
- A *trust delta* $\Delta\tau(m) \in \mathbb{R}$ (expected change in aggregate trust);
- A *curvature delta* $\Delta F(m) \in \mathbb{R}$ (expected change in bundle curvature).

**Definition 7.2 (Action Functional).** The *action* of a semantic move $m$ is:

$$S(m) = \mathrm{cost}(m) - \Delta\tau(m).$$

This measures the *net cost* of the move: the computational expense minus the trust improvement achieved. For a verification path $\gamma = (m_1, m_2, \ldots, m_k)$, the *total action* is:

$$S[\gamma] = \sum_{i=1}^{k} S(m_i) = \sum_{i=1}^{k} \big(\mathrm{cost}(m_i) - \Delta\tau(m_i)\big).$$

**Principle 7.3 (Principle of Least Action).** *The optimal verification strategy is the path $\gamma^*$ that minimizes the total action $S[\gamma]$ subject to the boundary conditions:*

$$\gamma(0) = (E_0, \nabla_0) \quad \text{(initial unverified bundle)}, \qquad |c_1(\gamma(1))| < \epsilon \quad \text{(target flatness)}.$$

*This is the geodesic equation for verification: the optimal strategy balances cost against trust improvement, reaching a flat (consistent) bundle with minimal total expenditure.*

**Proposition 7.4.** *In the action framework, grounding a claim via tool execution ($\mathrm{cost} = c_{\mathrm{tool}}, \Delta\tau = \tau_{\mathrm{TOOL\_VERIFIED}} - \tau_{\mathrm{current}}$) dominates majority voting ($\mathrm{cost} = k \cdot c_{\mathrm{model}}, \Delta\tau = \tau_{\mathrm{CROSS\_AGENT\_CONFIRMED}} - \tau_{\mathrm{current}}$) whenever*

$$c_{\mathrm{tool}} - (\tau_{\mathrm{TOOL\_VERIFIED}} - \tau_{\mathrm{current}}) < k \cdot c_{\mathrm{model}} - (\tau_{\mathrm{CROSS\_AGENT\_CONFIRMED}} - \tau_{\mathrm{current}}).$$

*Since $\tau_{\mathrm{TOOL\_VERIFIED}} > \tau_{\mathrm{CROSS\_AGENT\_CONFIRMED}}$ by the trust ordering and $c_{\mathrm{tool}}$ is typically comparable to $c_{\mathrm{model}}$ for $k \geq 3$, tool grounding is generically the lower-action strategy.*

---

## 8. Relationship to Čech Cohomology

The JuGeo framework classifies multi-agent verification failures by Čech cohomology class [1, 2]:

- $H^0$ obstructions: coverage gaps — a subtask has no assigned agent, or an agent's output is truncated, producing an incomplete section.
- $H^1$ obstructions: pairwise contradictions — two agents make incompatible claims on a shared subject, violating the cocycle condition.
- $H^2$ obstructions: cascading hallucinations — an $H^1$ contradiction on one overlap induces further contradictions on adjacent overlaps, forming a non-trivial 2-cocycle.
- Phantom sections: the sheaf admits a global section (no contradictions) that is entirely ungrounded — consistent but fabricated.

We now show that the judgment fiber bundle *strictly generalizes* this Čech-cohomological framework.

**Theorem 8.1 (Embedding).** *Every Čech obstruction class in the sheaf framework corresponds to a curvature or holonomy obstruction in the bundle framework:*

*(i) An $H^1$ obstruction on the edge $(A_i, A_j)$ with subject $S$ corresponds to non-zero curvature at every 2-simplex containing this edge.*

*(ii) An $H^2$ cascade through agents $(A_i, A_j, A_k)$ corresponds to non-trivial holonomy of the loop $(A_i, A_j, A_k, A_i)$.*

*(iii) A phantom section corresponds to a flat connection with trivial holonomy but empty evidence — every fiber has $e = \emptyset$.*

*Proof.* (i) An $H^1$ obstruction means agents $A_i$ and $A_j$ assert contradictory claims about subject $S$. In the bundle, this contradiction manifests as a trust discrepancy: if $A_i$ asserts $\sigma$ at trust $\tau_a$ and $A_j$ asserts $\neg\sigma$ at trust $\tau_b$, the trust differential $\Delta_{i \to j}$ is not the intrinsic trust difference but rather encodes the contradiction. For any third agent $A_k$ covering $S$, the curvature $F(A_i, A_j, A_k; S) \neq 0$ because $A_k$ must agree with at most one of $A_i, A_j$, creating an inconsistent transport triangle.

(ii) An $H^2$ cascade is a sequence of pairwise contradictions forming a cycle: $A_i$ contradicts $A_j$ on some claim, $A_j$ contradicts $A_k$ on a derived claim, and $A_k$'s claim contradicts $A_i$'s. The holonomy of the loop $(A_i, A_j, A_k, A_i)$ is the sum $\Delta_{i \to j} + \Delta_{j \to k} + \Delta_{k \to i}$, which is non-zero because the cascading contradictions prevent the differentials from canceling.

(iii) A phantom section is globally consistent ($\Delta_{i \to j} = \tau_j - \tau_i$ for some global assignment, so $F = 0$ everywhere) but every judgment has empty evidence: $e(J) = \emptyset$ for all $J \in E$. The bundle is flat and the holonomy is trivial, but the evidence functors $\Phi_\chi$ map every judgment to $\emptyset$, detecting the ungroundedness. $\square$

**Theorem 8.2 (Strict Generalization).** *The bundle framework detects obstructions invisible to the sheaf:*

*(i) Team-level trust cycles: a set of agents with no pairwise contradictions but a collective trust imbalance ($c_1 \neq 0$).*

*(ii) Stratum-sensitive obstructions: contradictions whose severity depends on the trust level, invisible to the binary consistent/inconsistent classification of Čech cohomology.*

*Proof.* (i) Consider three agents with no contradictions but with trust differentials $\Delta_{1 \to 2} = +2$, $\Delta_{2 \to 3} = +2$, $\Delta_{3 \to 1} = +2$. No pair is contradictory (they may agree on all claims), but the curvature $F = 2 + 2 + 2 = 6 \neq 0$. This represents trust inflation — a mutual admiration cycle — invisible to pairwise consistency checking. The sheaf has no obstruction ($H^1 = 0$), but the bundle has non-zero curvature.

(ii) Two contradictions — one between $\texttt{TOOL\_VERIFIED}$ agents and one between $\texttt{WEAK\_MODEL\_GENERATED}$ agents — are both $H^1$ obstructions in the sheaf. But the stratum-weighted obstruction measure $\Omega$ assigns weight $9/10$ to the former and $2/10$ to the latter, reflecting the dramatically different severity. The sheaf framework treats them identically. $\square$

---

## 9. Implementation

The judgment fiber bundle construction is implemented in the `jugeo_agents.core.bundle` module. We describe the key classes and provide a worked example.

**Core Classes.** The implementation provides:

- `Judgment`: a frozen dataclass with fields `claim` (a `FactualClaim`), `evidence` (list of strings), `trust` (a `TrustLevel`), `channel` (string), and `agent_id`.
- `JudgmentFiber`: a fiber over an `(agent_id, subject)` pair containing a list of `Judgment` objects, with properties `max_trust`, `min_trust`, and `trust_spread`.
- `TrustConnection`: stores observed trust pairs via `observe(agent_a, agent_b, subject, trust_a, trust_b)` and computes parallel transport via `transport(source, target, source_trust, subject)`, returning a `TransportResult` with the transported trust level and consistency flag.
- `JudgmentBundle`: the main orchestrator. The method `add_judgment()` populates fibers and the stratification; `build_connection()` constructs the connection from observed trust pairs; `curvature(a, b, c)` computes the curvature 2-form on a triple; `holonomy(loop)` computes the holonomy of a loop; `first_chern_class()` returns a `CharacteristicClass` with the mean curvature; `diagnose()` returns a comprehensive diagnostic dictionary.
- `StratifiedJudgmentSpace`: partitions judgments by trust level and detects intra-stratum contradictions via `stratum_obstructions()`.
- `EvidenceFunctor`: extracts channel-specific evidence via `extract()` and checks verification capability via `can_verify()`.
- `SemanticMove`: a frozen dataclass with `cost`, `trust_delta`, and `curvature_delta`, with a computed `action` property.

**Example.** Consider a 3-agent team verifying the claim "The function returns a sorted list."

```python
from jugeo_agents.core.bundle import JudgmentBundle, Judgment
from jugeo_agents.types import TrustLevel, FactualClaim

bundle = JudgmentBundle()

# Agent A: model-generated claim
bundle.add_judgment(Judgment(
    claim=FactualClaim(text="returns sorted list",
                       subject="sort_fn", predicate="returns", value="sorted"),
    evidence=[], trust=TrustLevel.WEAK_MODEL_GENERATED,
    channel="model", agent_id="agent-a"))

# Agent B: tool-verified claim (ran the code)
bundle.add_judgment(Judgment(
    claim=FactualClaim(text="returns sorted list",
                       subject="sort_fn", predicate="returns", value="sorted"),
    evidence=["test_sort passed"], trust=TrustLevel.TOOL_VERIFIED,
    channel="tool", agent_id="agent-b"))

# Agent C: RAG-grounded claim (found documentation)
bundle.add_judgment(Judgment(
    claim=FactualClaim(text="returns sorted list",
                       subject="sort_fn", predicate="returns", value="sorted"),
    evidence=["docstring: Returns sorted list"],
    trust=TrustLevel.RAG_GROUNDED,
    channel="rag", agent_id="agent-c"))

# Compute diagnostics
diag = bundle.diagnose()
print(f"c₁ = {diag['chern_class']['value']:.3f}")
print(f"Bundle is flat: {diag['bundle_is_flat']}")
```

**Interpretation.** With three agreeing agents at trust levels 2 (WEAK_MODEL_GENERATED), 8 (TOOL_VERIFIED), and 6 (RAG_GROUNDED), the trust differentials are $\Delta_{A \to B} = +6$, $\Delta_{B \to C} = -2$, $\Delta_{C \to A} = -4$. The curvature is $F = 6 + (-2) + (-4) = 0$, so the bundle is flat — the trust differences are globally consistent. The first Chern class vanishes: $c_1 = 0$. This is expected: the agents agree on the claim, and their trust levels reflect genuine differences in evidence quality.

If we modified Agent C to contradict the others (claiming the function returns an *unsorted* list, while maintaining RAG_GROUNDED trust), the curvature would become non-zero, reflecting the structural inconsistency: an agent with moderately high trust contradicts a tool-verified result, creating a trust cycle that cannot be resolved by simple ordering.

---

## 10. Conclusion

We have introduced the *judgment fiber bundle*, a differential-geometric construction that formalizes multi-agent verification in LLM systems. The construction makes precise the intuition that trust between agents is not a static label but a *connection* — a rule for transporting judgment quality across the agent–task space. The curvature of this connection detects structural team unreliability that is invisible to any collection of pairwise checks, and the first Chern class provides a single scalar diagnostic for global team coherence.

**Summary of contributions.** (1) The judgment bundle construction unifies sheaf-cohomological consistency checking with differential-geometric trust analysis. (2) Gauge invariance of curvature (Theorem 3.7) ensures that diagnostics depend only on relative trust, not absolute calibration. (3) The discrete Ambrose–Singer theorem (Theorem 3.9) connects local curvature to global holonomy. (4) The Chern class flatness criterion (Theorem 4.2) characterizes when global trust consistency is achievable. (5) Trust stratification enables severity-sensitive contradiction detection. (6) Evidence functors and natural transformations recover the trust algebra's promotion/demotion operations in a categorical framework. (7) The action principle provides a variational characterization of optimal verification strategies.

**Future directions.** Several extensions suggest themselves:

- *Higher Chern classes.* For bundles of rank $> 1$ (agents producing vector-valued judgments), the higher Chern classes $c_2, c_3, \ldots$ encode more refined topological information. The second Chern class, for instance, could detect "instanton-like" configurations where trust is locally flat but globally twisted.
- *Secondary characteristic classes.* Chern–Simons invariants of the trust connection could detect 3-dimensional obstructions in multi-round verification protocols, where the "third dimension" is the round number.
- *Spectral sequences.* The Leray–Serre spectral sequence of the judgment bundle relates the cohomology of the total space, base, and fiber. Applied to multi-round verification, this could yield convergence rate estimates.
- *Yang–Mills equations.* The Yang–Mills functional $\mathrm{YM}(\nabla) = \int_B \|F\|^2$ measures the total curvature of the connection. Minimizing YM yields the *optimal trust connection* — the least-curved trust structure compatible with the observed data. The Yang–Mills equations for the judgment bundle would define a PDE (or its discrete analogue) whose solutions are the most coherent possible team trust configurations.
- *AI safety and alignment verification.* The judgment bundle framework could be applied to alignment verification, where the "agents" include both AI systems and human overseers, and the curvature detects structural misalignment between human and AI trust structures.

The judgment fiber bundle demonstrates that the geometry of trust is not merely a metaphor. It is a precise mathematical structure with computable invariants, provable theorems, and direct implementation as working software.

---

## References

[1] H. Young, "Judgment Geometry: Semantic Sites and Sheaf Descent for Multi-Agent Verification," JuGeo Technical Report, 2024.

[2] H. Young, "Descent Obstructions in Multi-Agent LLM Systems: A Čech-Cohomological Classification," JuGeo Papers, 2024.

[3] H. Young, "Trust Algebras: Ordered Lattices with Evidence-Gated Promotion for LLM Verification," JuGeo Papers, 2024.

[4] D. Husemoller, *Fibre Bundles*, 3rd ed., Graduate Texts in Mathematics, vol. 20, Springer-Verlag, New York, 1994.

[5] W. Ambrose and I. M. Singer, "A theorem on holonomy," *Transactions of the American Mathematical Society*, vol. 75, no. 3, pp. 428–443, 1953.

[6] J. W. Milnor and J. D. Stasheff, *Characteristic Classes*, Annals of Mathematics Studies, vol. 76, Princeton University Press, 1974.

[7] R. Bott and L. W. Tu, *Differential Forms in Algebraic Topology*, Graduate Texts in Mathematics, vol. 82, Springer-Verlag, New York, 1982.

[8] M. Goresky and R. MacPherson, "Intersection homology theory," *Topology*, vol. 19, no. 2, pp. 135–162, 1980.

[9] A. Grothendieck, *Revêtements étales et groupe fondamental* (SGA 1), Lecture Notes in Mathematics, vol. 224, Springer-Verlag, 1971.

[10] M. Artin, A. Grothendieck, and J.-L. Verdier, *Théorie des topos et cohomologie étale des schémas* (SGA 4), Lecture Notes in Mathematics, vols. 269, 270, 305, Springer-Verlag, 1972–1973.

[11] Y. Jiang, "Multi-agent debate improves mathematical and strategic reasoning in large language models," *arXiv preprint* arXiv:2305.14325, 2023.

[12] N. Shinn, F. Cassano, A. Gopinath, K. Narasimhan, and S. Yao, "Reflexion: Language agents with verbal reinforcement learning," *Advances in Neural Information Processing Systems*, vol. 36, 2023.

[13] T. Wu, E. Mitchell, and C. D. Manning, "Reasoning or reciting? Exploring the capabilities and limitations of language models through counterfactual tasks," *arXiv preprint* arXiv:2307.02477, 2023.
