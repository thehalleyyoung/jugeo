r"""Normal form computation and caching for the JuGeo IR stack (theory2.tex Ch32 §3).

This module implements the normal form computation pipeline for the JuGeo
intermediate representation.  The key correctness property of the reduction
system is the *Church-Rosser* (confluence) property, expressed by the
diamond condition:

.. math::

   M \twoheadrightarrow N_1, \quad M \twoheadrightarrow N_2 \implies
   \exists\, P :\; N_1 \twoheadrightarrow P,\; N_2 \twoheadrightarrow P

where :math:`\twoheadrightarrow` denotes the multi-step reduction relation.
Confluence guarantees that the choice of reduction strategy does not affect
the final normal form, so cached results computed under one strategy remain
valid for queries from another strategy.

Architecture
------------
Normal form computation is split into five collaborating components:

- :class:`ReductionStrategy` — encapsulates a reduction *order* (head-normal,
  full-normal, or weak-head) together with a step budget and laziness flag.
  The strategy is the entry point for all reduction calls.
- :class:`ReductionRule` — a single rewrite rule with a pattern, a replacement
  template, and a list of side conditions.  Rules carry an integer priority for
  ordering when multiple rules match the same redex.
- :class:`ConfluenceChecker` — checks the diamond property locally (one-step
  reductions from a single node) and globally (across a list of nodes), and
  generates proof obligations for critical pairs.
- :class:`NormalFormCache` — an LRU-evicting cache mapping canonical keys to
  computed :class:`~jugeo.encodings.ir_stack.models.NormalForm` objects.
- :class:`CanonicalHasher` — computes deterministic, order-independent hashes
  for IR nodes and layers, providing the cache keys consumed by
  :class:`NormalFormCache`.

References
----------
theory2.tex Ch32 §3 — Normal Form Computation and Caching, pp. 342–378.
"""

from __future__ import annotations

import collections
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from jugeo.encodings.ir_stack.models import (
        IRNode, IRLayer, IRStack, NormalForm, NormalFormKind, IRNodeKind,
    )
except ImportError:
    pass  # runtime stubs provided by try/except blocks below

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula
except ImportError:
    class Z3Session:  # type: ignore[no-redef]
        pass

    class Z3Formula:  # type: ignore[no-redef]
        pass


# ===================================================================== #
# Module-level normal form cache (singleton for this process)            #
# ===================================================================== #

_GLOBAL_NF_CACHE: dict[str, Any] = {}  # key -> NormalForm-like object


# ===================================================================== #
# Section 1: Reduction strategies (head-normal, full-normal, weak-head)  #
# ===================================================================== #

@dataclass
class ReductionStrategy:
    """Encapsulates a strategy for reducing IR terms to normal forms.

    Three canonical strategies are supported, matching the kinds enumerated
    in :class:`~jugeo.encodings.ir_stack.models.NormalFormKind`:

    * **Head-normal** — repeatedly reduce the leftmost-outermost redex until
      the head position is not reducible.  Subterms may remain unreduced.
    * **Full-normal** — reduce all subterms to normal form, bottom-up.  This
      is the most expensive strategy but produces the most compact result.
    * **Weak-head** — reduce the head position only one step, without
      descending into subterms.  Used by lazy evaluation strategies.

    The step budget ``max_steps`` prevents non-termination for terms that do
    not have a finite normal form under the chosen strategy.

    Attributes
    ----------
    strategy_id:
        Unique identifier for this strategy instance.
    strategy_kind:
        The kind of normal form targeted by this strategy.  A
        :class:`~jugeo.encodings.ir_stack.models.NormalFormKind` value, or a
        plain string when the models package is unavailable.
    max_steps:
        Maximum number of single-step reductions before the strategy gives up.
        A value of ``0`` means unbounded (use with caution for non-SN terms).
    is_lazy:
        When ``True``, subterms inside lambdas are not reduced until demanded.
    _step_count:
        Internal counter tracking reductions performed in the current session.
        Reset by :meth:`reset`.
    """

    strategy_id: str
    strategy_kind: Any  # NormalFormKind when models available
    max_steps: int
    is_lazy: bool
    _step_count: int

    def _get_kind_of(self, node: Any) -> str:
        """Extract the node kind as a string for dispatch logic."""
        kind = getattr(node, "kind", None)
        if kind is None:
            return "unknown"
        return str(kind.value) if hasattr(kind, "value") else str(kind)

    def _get_children(self, node: Any) -> list[Any]:
        """Return the list of direct child nodes of *node*."""
        children = getattr(node, "children", None)
        if isinstance(children, list):
            return children
        payload = getattr(node, "payload", {}) or {}
        child_list = payload.get("children", [])
        return child_list if isinstance(child_list, list) else []

    def _is_redex(self, node: Any) -> bool:
        """Heuristic: a node is a redex if it is an application of a lambda."""
        kind = self._get_kind_of(node)
        if kind in ("application", "app", "apply", "beta_redex"):
            payload = getattr(node, "payload", {}) or {}
            func = payload.get("func") or payload.get("function")
            if func is not None:
                func_kind = self._get_kind_of(func) if not isinstance(func, str) else func
                return str(func_kind) in ("lambda", "abstraction", "lam", "fn")
        return False

    def _beta_reduce_one(self, node: Any) -> Any | None:
        """Apply one beta-reduction step to *node*.

        Extracts the parameter name and body from the lambda, then substitutes
        the argument throughout the body.  Returns the reduced node, or
        ``None`` if node is not a beta redex.
        """
        if not self._is_redex(node):
            return None
        payload = getattr(node, "payload", {}) or {}
        func = payload.get("func") or payload.get("function")
        arg = payload.get("arg") or payload.get("argument")
        if func is None or arg is None:
            return None
        func_payload = getattr(func, "payload", {}) or {}
        param = func_payload.get("param") or func_payload.get("parameter", "__x")
        body = func_payload.get("body")
        if body is None:
            return None
        substituted = _substitute(body, str(param), arg)
        self._step_count += 1
        return substituted

    def reduce_head(self, node: Any) -> tuple[Any, bool]:
        """Apply one head-reduction step to *node*.

        The head position is the leftmost-outermost redex.  If the node itself
        is a redex, it is reduced.  Otherwise the reduction descends into the
        function position of an application.  Returns a tuple
        ``(possibly_reduced_node, did_reduce)``.

        Parameters
        ----------
        node:
            The IR node to attempt head-reduction on.
        """
        if self.max_steps > 0 and self._step_count >= self.max_steps:
            return node, False
        reduced = self._beta_reduce_one(node)
        if reduced is not None:
            return reduced, True
        # Descend into function position
        payload = getattr(node, "payload", {}) or {}
        func = payload.get("func") or payload.get("function")
        if func is not None:
            new_func, did = self.reduce_head(func)
            if did:
                new_payload = dict(payload)
                key = "func" if "func" in payload else "function"
                new_payload[key] = new_func
                return _node_with_payload(node, new_payload), True
        return node, False

    def reduce_full(self, node: Any) -> Any:
        """Fully reduce all subterms of *node* to normal form.

        Applies a post-order traversal: children are fully reduced before the
        node itself is head-reduced.  Stops when the budget is exhausted or
        when no further reductions are possible.

        Parameters
        ----------
        node:
            The IR node to fully normalise.
        """
        if self.max_steps > 0 and self._step_count >= self.max_steps:
            return node
        children = self._get_children(node)
        new_children = [self.reduce_full(c) for c in children]
        node = _node_with_children(node, new_children)
        changed = True
        while changed:
            if self.max_steps > 0 and self._step_count >= self.max_steps:
                break
            node, changed = self.reduce_head(node)
        return node

    def reduce_weak_head(self, node: Any) -> tuple[Any, bool]:
        """Reduce *node* to weak head normal form (WHNF).

        WHNF is reached when the outermost constructor is not a redex.
        Unlike full normalisation, subterms inside constructors are left
        unreduced.  Returns ``(node_in_whnf, did_reduce)``.

        Parameters
        ----------
        node:
            The IR node to reduce.
        """
        if self.max_steps > 0 and self._step_count >= self.max_steps:
            return node, False
        did_any = False
        changed = True
        while changed:
            if self.max_steps > 0 and self._step_count >= self.max_steps:
                break
            node, changed = self.reduce_head(node)
            if changed:
                did_any = True
            # Stop once the head position is a non-redex constructor
            if not self._is_redex(node):
                break
        return node, did_any

    def is_head_normal(self, node: Any) -> bool:
        """Return ``True`` if *node* is already in head normal form.

        A node is in HNF when its head position is not a redex (i.e. the
        leftmost-outermost redex does not exist).

        Parameters
        ----------
        node:
            The IR node to check.
        """
        if self._is_redex(node):
            return False
        payload = getattr(node, "payload", {}) or {}
        func = payload.get("func") or payload.get("function")
        if func is not None:
            return self.is_head_normal(func)
        return True

    def is_fully_normal(self, node: Any) -> bool:
        """Return ``True`` if *node* and all its subterms are in normal form.

        Performs a complete recursive descent.  Any redex anywhere in the
        tree causes the method to return ``False`` immediately.

        Parameters
        ----------
        node:
            The IR node to check.
        """
        if not self.is_head_normal(node):
            return False
        for child in self._get_children(node):
            if not self.is_fully_normal(child):
                return False
        payload = getattr(node, "payload", {}) or {}
        for key in ("func", "function", "arg", "argument", "body"):
            sub = payload.get(key)
            if sub is not None and not isinstance(sub, (str, int, float, bool)):
                if not self.is_fully_normal(sub):
                    return False
        return True

    def step_count(self) -> int:
        """Return the number of reduction steps taken since the last reset."""
        return self._step_count

    def reset(self) -> None:
        """Reset the step counter to zero for a fresh reduction session."""
        self._step_count = 0


# ===================================================================== #
# Section 2: Reduction rules                                             #
# ===================================================================== #

@dataclass
class ReductionRule:
    """A single named reduction rule with a pattern, replacement, and conditions.

    Reduction rules are the primitive components of a rewriting system.  A
    rule matches when its ``pattern`` unifies with a given node (via shallow
    structural matching) and all ``conditions`` hold.  The ``replacement``
    dict specifies how to construct the reduced node from matched sub-terms.

    Rules carry an integer ``priority`` for disambiguation when multiple
    rules match the same redex.  Lower numeric values mean higher priority.

    Attributes
    ----------
    rule_id:
        Unique identifier for this rule.
    rule_name:
        Human-readable name, e.g. ``"beta"`` or ``"eta"`` or ``"delta"``.
    pattern:
        Shallow pattern dict; keys are node attributes and values are
        either concrete values (exact match) or ``"?"`` (wildcard).
    replacement:
        Template dict describing the replacement node.  Keys starting with
        ``"$"`` are substitution variables bound during pattern matching.
    conditions:
        List of condition dicts, each with a ``"kind"`` key and operand keys.
        All conditions must hold for the rule to fire.
    priority:
        Tie-breaking priority; lower is higher priority.
    """

    rule_id: str
    rule_name: str
    pattern: dict[str, Any]
    replacement: dict[str, Any]
    conditions: list[dict]
    priority: int

    def _match_pattern(self, node: Any) -> dict[str, Any] | None:
        """Attempt to unify *node* against ``self.pattern``.

        Returns a bindings dict mapping wildcard names to matched values on
        success, or ``None`` on failure.  Wildcards in the pattern are marked
        with the ``"?"`` sentinel; other values must match exactly (by
        ``str()`` comparison).
        """
        bindings: dict[str, Any] = {}
        payload = getattr(node, "payload", {}) or {}
        for key, expected in self.pattern.items():
            if key == "kind":
                node_kind = getattr(node, "kind", None)
                actual = str(node_kind.value) if hasattr(node_kind, "value") else str(node_kind)
            else:
                actual = payload.get(key)
            if expected == "?":
                bindings[key] = actual
            elif str(actual) != str(expected):
                return None
        return bindings

    def matches(self, node: Any) -> bool:
        """Return ``True`` if this rule's pattern matches *node*.

        Does *not* check conditions; use :meth:`check_conditions` separately
        when a full eligibility check is required.

        Parameters
        ----------
        node:
            The IR node to test the pattern against.
        """
        return self._match_pattern(node) is not None

    def apply(self, node: Any) -> Any | None:
        """Apply this rule to *node* and return the rewritten node.

        Returns ``None`` if the pattern does not match or conditions fail.
        The replacement is built by copying the ``replacement`` dict and
        filling in any ``"$key"`` template variables from the match bindings.

        Parameters
        ----------
        node:
            The IR node to rewrite.
        """
        bindings = self._match_pattern(node)
        if bindings is None:
            return None
        if not self.check_conditions(node):
            return None
        filled_replacement: dict[str, Any] = {}
        for rkey, rval in self.replacement.items():
            if isinstance(rval, str) and rval.startswith("$"):
                var_name = rval[1:]
                filled_replacement[rkey] = bindings.get(var_name, rval)
            else:
                filled_replacement[rkey] = rval
        return _node_with_payload(node, filled_replacement)

    def apply_all(self, root: Any) -> tuple[Any, int]:
        """Apply this rule everywhere in the tree rooted at *root*.

        Performs a post-order traversal: children are rewritten before the
        node itself.  Returns a tuple ``(new_root, count_applied)`` where
        *count_applied* is the number of successful rule applications.

        Parameters
        ----------
        root:
            The root IR node of the tree.
        """
        count = 0
        children = _get_children_any(root)
        new_children = []
        for child in children:
            new_child, child_count = self.apply_all(child)
            new_children.append(new_child)
            count += child_count
        root = _node_with_children(root, new_children)
        rewritten = self.apply(root)
        if rewritten is not None:
            root = rewritten
            count += 1
        return root, count

    def check_conditions(self, node: Any) -> bool:
        """Verify all conditions hold for *node*.

        Currently supports ``"not_free_in"`` (a name is not free in a
        subterm) and ``"kind_is"`` (the node kind matches a given value)
        conditions.  Unknown condition kinds are treated as vacuously true
        to allow forward compatibility.

        Parameters
        ----------
        node:
            The IR node being considered for rewriting.
        """
        payload = getattr(node, "payload", {}) or {}
        for condition in self.conditions:
            kind = condition.get("kind", "")
            if kind == "kind_is":
                expected_kind = condition.get("value", "")
                node_kind = getattr(node, "kind", None)
                actual = str(node_kind.value) if hasattr(node_kind, "value") else str(node_kind)
                if actual != str(expected_kind):
                    return False
            elif kind == "not_free_in":
                var_name = condition.get("var", "")
                subterm_key = condition.get("subterm", "body")
                subterm = payload.get(subterm_key)
                if subterm is not None and _name_is_free(subterm, var_name):
                    return False
            elif kind == "payload_key_exists":
                key = condition.get("key", "")
                if key not in payload:
                    return False
            # Unknown condition kinds pass vacuously
        return True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this rule."""
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "pattern": self.pattern,
            "replacement": self.replacement,
            "conditions": self.conditions,
            "priority": self.priority,
        }

    def priority_compare(self, other: ReductionRule) -> int:
        """Return -1, 0, or 1 based on priority ordering with *other*.

        Lower numeric priority value → higher priority (fires first).
        Returns ``-1`` when ``self`` should fire before *other*, ``1``
        when *other* fires first, and ``0`` when priorities are equal.

        Parameters
        ----------
        other:
            The rule to compare against.
        """
        if self.priority < other.priority:
            return -1
        if self.priority > other.priority:
            return 1
        return 0


# ===================================================================== #
# Section 3: Confluence checking                                         #
# ===================================================================== #

@dataclass
class ConfluenceChecker:
    """Checks the Church-Rosser / confluence property for a rule set.

    The checker works at two levels:

    1. **Local confluence** (diamond property for one-step reductions):
       given a node *M* and all one-step reducts :math:`N_1, N_2`, checks
       that :math:`N_1` and :math:`N_2` share a common reduct in one or
       more steps.

    2. **Global confluence**: checks local confluence for a list of nodes
       and returns ``True`` only if all nodes satisfy the diamond property.

    Critical pairs are pairs of one-step reducts that have been generated by
    overlapping redexes; the checker finds and records them in
    ``_proof_obligations``.

    Attributes
    ----------
    checker_id:
        Unique identifier for this checker instance.
    rules:
        The current rewriting rule set to check confluence for.
    _proof_obligations:
        List of proof-obligation dicts, each recording a critical pair and
        whether it has been closed (i.e. a common reduct was found).
    _confluence_cache:
        Maps node hash strings to confluence results to avoid redundant work.
    """

    checker_id: str
    rules: list[ReductionRule]
    _proof_obligations: list[dict]
    _confluence_cache: dict[str, bool]

    def _one_step_reducts(self, node: Any) -> list[Any]:
        """Compute all distinct one-step reducts of *node* under ``self.rules``.

        Each rule that matches *node* generates one reduct.  Rules are applied
        in priority order.  Duplicate reducts (same JSON fingerprint) are
        deduplicated.
        """
        sorted_rules = sorted(self.rules, key=lambda r: r.priority)
        seen_reprs: set[str] = set()
        reducts: list[Any] = []
        for rule in sorted_rules:
            reduct = rule.apply(node)
            if reduct is not None:
                repr_key = json.dumps(
                    getattr(reduct, "payload", {}), sort_keys=True, default=str
                )
                if repr_key not in seen_reprs:
                    seen_reprs.add(repr_key)
                    reducts.append(reduct)
        return reducts

    def _multi_step_reduce(self, node: Any, budget: int = 50) -> Any:
        """Reduce *node* as far as possible within *budget* steps."""
        strategy = ReductionStrategy(
            strategy_id=str(uuid.uuid4()),
            strategy_kind="full_normal",
            max_steps=budget,
            is_lazy=False,
            _step_count=0,
        )
        return strategy.reduce_full(node)

    def _node_fingerprint(self, node: Any) -> str:
        """Return a stable JSON fingerprint for *node* for comparison."""
        payload = getattr(node, "payload", {}) or {}
        kind = str(getattr(node, "kind", ""))
        return json.dumps({"kind": kind, "payload": payload}, sort_keys=True, default=str)

    def check_local_confluence(self, node: Any) -> bool:
        """Check the local diamond property for all one-step reductions from *node*.

        For each pair of one-step reducts :math:`(N_1, N_2)`, both are
        further reduced to a common normal form and fingerprints are compared.
        Returns ``True`` if every pair shares a common reduct.

        Parameters
        ----------
        node:
            The node to check local confluence for.
        """
        fp = self._node_fingerprint(node)
        if fp in self._confluence_cache:
            return self._confluence_cache[fp]

        reducts = self._one_step_reducts(node)
        if len(reducts) <= 1:
            self._confluence_cache[fp] = True
            return True

        normal_forms = [self._node_fingerprint(self._multi_step_reduce(r)) for r in reducts]
        # All pairs must share a common normal form
        reference_nf = normal_forms[0]
        all_join = all(nf == reference_nf for nf in normal_forms)
        if not all_join:
            self._proof_obligations.append({
                "node_fingerprint": fp,
                "reducts": [self._node_fingerprint(r) for r in reducts],
                "normal_forms": normal_forms,
                "closed": False,
                "recorded_at": time.time(),
            })
        self._confluence_cache[fp] = all_join
        return all_join

    def check_global_confluence(self, nodes: list[Any]) -> bool:
        """Check confluence for all nodes in the provided list.

        Returns ``True`` only if every node passes :meth:`check_local_confluence`.

        Parameters
        ----------
        nodes:
            The list of IR nodes to check.
        """
        return all(self.check_local_confluence(node) for node in nodes)

    def find_critical_pairs(self) -> list[tuple[dict, dict]]:
        """Find all critical pairs among the current rule set.

        A critical pair arises when two rules have overlapping left-hand sides
        (i.e. both can fire at the same node).  Each critical pair is returned
        as a tuple of two replacement template dicts.

        Returns
        -------
        list[tuple[dict, dict]]
            Each element is a pair of replacement dicts from overlapping rules.
        """
        pairs: list[tuple[dict, dict]] = []
        for i, rule_a in enumerate(self.rules):
            for j, rule_b in enumerate(self.rules):
                if j <= i:
                    continue
                # Two rules overlap if their pattern keys share a common "kind" constraint
                kind_a = rule_a.pattern.get("kind")
                kind_b = rule_b.pattern.get("kind")
                if kind_a is not None and kind_b is not None and kind_a == kind_b:
                    pairs.append((rule_a.replacement, rule_b.replacement))
                elif kind_a is None or kind_b is None:
                    # One rule has a wildcard kind — it overlaps with everything
                    pairs.append((rule_a.replacement, rule_b.replacement))
        return pairs

    def add_rule(self, rule: ReductionRule) -> None:
        """Add *rule* to the rule set and record any new critical pairs.

        Critical pairs with each existing rule are computed and stored in
        ``_proof_obligations`` with ``"closed": None`` (not yet verified).

        Parameters
        ----------
        rule:
            The new rule to integrate.
        """
        for existing in self.rules:
            kind_new = rule.pattern.get("kind")
            kind_ex = existing.pattern.get("kind")
            if kind_new == kind_ex or kind_new is None or kind_ex is None:
                self._proof_obligations.append({
                    "kind": "critical_pair",
                    "rule_a": rule.rule_id,
                    "rule_b": existing.rule_id,
                    "replacement_a": rule.replacement,
                    "replacement_b": existing.replacement,
                    "closed": None,
                    "recorded_at": time.time(),
                })
        self.rules.append(rule)

    def generate_confluence_proof(
        self,
        node1: Any,
        node2: Any,
    ) -> dict[str, Any]:
        """Attempt to find a common reduct for *node1* and *node2*.

        Both nodes are fully reduced and their fingerprints are compared.
        The returned dict records whether a common reduct was found, the
        fingerprints of the two normal forms, and the number of steps taken.

        Parameters
        ----------
        node1:
            First node (e.g. :math:`N_1` from a critical pair).
        node2:
            Second node (e.g. :math:`N_2` from a critical pair).
        """
        strategy1 = ReductionStrategy(
            strategy_id=str(uuid.uuid4()),
            strategy_kind="full_normal",
            max_steps=200,
            is_lazy=False,
            _step_count=0,
        )
        strategy2 = ReductionStrategy(
            strategy_id=str(uuid.uuid4()),
            strategy_kind="full_normal",
            max_steps=200,
            is_lazy=False,
            _step_count=0,
        )
        nf1 = strategy1.reduce_full(node1)
        nf2 = strategy2.reduce_full(node2)
        fp1 = self._node_fingerprint(nf1)
        fp2 = self._node_fingerprint(nf2)
        joined = fp1 == fp2
        return {
            "success": joined,
            "normal_form_1": fp1,
            "normal_form_2": fp2,
            "steps_1": strategy1.step_count(),
            "steps_2": strategy2.step_count(),
            "generated_at": time.time(),
        }

    def confluence_report(self) -> dict[str, Any]:
        """Return a summary dict of the current confluence status.

        Includes counts of open vs. closed proof obligations and the number
        of critical pairs found.
        """
        total = len(self._proof_obligations)
        closed = sum(1 for po in self._proof_obligations if po.get("closed") is True)
        open_count = sum(1 for po in self._proof_obligations if po.get("closed") is False)
        unverified = total - closed - open_count
        critical_pair_count = len(self.find_critical_pairs())
        return {
            "checker_id": self.checker_id,
            "rule_count": len(self.rules),
            "proof_obligations_total": total,
            "proof_obligations_closed": closed,
            "proof_obligations_open": open_count,
            "proof_obligations_unverified": unverified,
            "critical_pairs": critical_pair_count,
            "cache_entries": len(self._confluence_cache),
            "generated_at": time.time(),
        }


# ===================================================================== #
# Section 4: Cache-key computation                                       #
# ===================================================================== #

@dataclass
class NormalFormCache:
    """LRU-evicting cache mapping canonical keys to computed normal forms.

    The cache tracks hit/miss counts for performance monitoring and evicts
    the least recently used entries when ``max_size`` is exceeded.  Access
    order is maintained via an :class:`collections.OrderedDict`.

    Attributes
    ----------
    cache_id:
        Unique identifier for this cache instance.
    _cache:
        Internal OrderedDict mapping key strings to NormalForm-like objects.
    _access_count:
        Maps cache keys to the number of times they have been accessed.
    _hit_count:
        Total number of successful cache lookups.
    _miss_count:
        Total number of failed cache lookups.
    max_size:
        Maximum number of entries before LRU eviction is triggered.
    """

    cache_id: str
    _cache: dict[str, Any]
    _access_count: dict[str, int]
    _hit_count: int
    _miss_count: int
    max_size: int

    # copilot: NormalFormCache.warm_up can be triggered by copilot to pre-cache likely reduction targets

    def __post_init__(self) -> None:
        """Upgrade internal _cache to an OrderedDict for LRU tracking."""
        if not isinstance(self._cache, collections.OrderedDict):
            self._cache = collections.OrderedDict(self._cache)

    def lookup(self, key: str) -> Any | None:
        """Return the cached normal form for *key*, or ``None`` on miss.

        On a hit, the entry is moved to the end (most-recently-used position)
        and ``_hit_count`` is incremented.  On a miss, ``_miss_count`` is
        incremented.

        Parameters
        ----------
        key:
            The canonical cache key for the desired normal form.
        """
        if key in self._cache:
            self._hit_count += 1
            self._access_count[key] = self._access_count.get(key, 0) + 1
            # Move to end (most recently used)
            self._cache.move_to_end(key)  # type: ignore[attr-defined]
            return self._cache[key]
        self._miss_count += 1
        return None

    def store(self, key: str, nf: Any) -> None:
        """Store *nf* under *key*, evicting the LRU entry if at capacity.

        If *key* already exists, the existing entry is updated in place and
        moved to the most-recently-used position.  Otherwise, if the cache
        is at or above ``max_size``, :meth:`evict_lru` is called to make room.

        Parameters
        ----------
        key:
            The canonical cache key.
        nf:
            The normal form object to cache.
        """
        if key in self._cache:
            self._cache.move_to_end(key)  # type: ignore[attr-defined]
            self._cache[key] = nf
            return
        if self.max_size > 0 and len(self._cache) >= self.max_size:
            self.evict_lru(count=1)
        self._cache[key] = nf
        self._access_count[key] = 0

    def invalidate(self, key: str) -> bool:
        """Remove *key* from the cache.

        Returns ``True`` if the entry existed and was removed, ``False``
        if the key was not in the cache.

        Parameters
        ----------
        key:
            The cache key to invalidate.
        """
        if key in self._cache:
            del self._cache[key]
            self._access_count.pop(key, None)
            return True
        return False

    def invalidate_prefix(self, prefix: str) -> int:
        """Remove all entries whose keys begin with *prefix*.

        Returns the count of entries removed.

        Parameters
        ----------
        prefix:
            The key prefix to match.  An empty prefix removes all entries.
        """
        keys_to_remove = [k for k in list(self._cache.keys()) if k.startswith(prefix)]
        for key in keys_to_remove:
            del self._cache[key]
            self._access_count.pop(key, None)
        return len(keys_to_remove)

    def hit_rate(self) -> float:
        """Return the cache hit rate as a float in ``[0.0, 1.0]``.

        Returns ``0.0`` when no lookups have been performed yet to avoid
        division-by-zero.
        """
        total = self._hit_count + self._miss_count
        if total == 0:
            return 0.0
        return self._hit_count / total

    def evict_lru(self, count: int = 1) -> int:
        """Evict the *count* least recently used entries.

        The LRU entry is the first element of the internal OrderedDict.
        Returns the number of entries actually evicted (may be less than
        *count* if the cache has fewer entries).

        Parameters
        ----------
        count:
            Number of entries to evict.  Defaults to 1.
        """
        evicted = 0
        for _ in range(count):
            if not self._cache:
                break
            lru_key, _ = next(iter(self._cache.items()))
            del self._cache[lru_key]
            self._access_count.pop(lru_key, None)
            evicted += 1
        return evicted

    def statistics(self) -> dict[str, Any]:
        """Return a comprehensive statistics dict for this cache."""
        return {
            "cache_id": self.cache_id,
            "size": len(self._cache),
            "max_size": self.max_size,
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "hit_rate": self.hit_rate(),
            "most_accessed": sorted(
                self._access_count.items(), key=lambda kv: kv[1], reverse=True
            )[:10],
        }

    def warm_up(self, nodes: list[Any], strategy: ReductionStrategy) -> int:
        """Pre-compute and cache normal forms for a list of nodes.

        For each node in *nodes*, computes its canonical cache key (using a
        temporary :class:`CanonicalHasher`), checks whether the result is
        already cached, and if not performs full reduction and stores the
        result.  Returns the count of new entries added to the cache.

        Parameters
        ----------
        nodes:
            The IR nodes to pre-compute normal forms for.
        strategy:
            The reduction strategy to use during pre-computation.
        """
        hasher = CanonicalHasher(
            hasher_id=str(uuid.uuid4()),
            hash_algorithm="sha256",
            _hash_cache={},
            include_trust_level=False,
        )
        added = 0
        for node in nodes:
            key = hasher.cache_key_for(node, strategy)
            if self.lookup(key) is None:
                strategy.reset()
                reduced = strategy.reduce_full(node)
                nf_dict: dict[str, Any] = {
                    "node_id": getattr(node, "node_id", str(uuid.uuid4())),
                    "reduced_payload": getattr(reduced, "payload", {}),
                    "strategy_kind": str(strategy.strategy_kind),
                    "steps": strategy.step_count(),
                    "cached_at": time.time(),
                }
                self.store(key, nf_dict)
                added += 1
        return added


# ===================================================================== #
# Section 5: Normal form comparison                                      #
# ===================================================================== #

@dataclass
class CanonicalHasher:
    """Computes deterministic canonical hashes for IR nodes and layers.

    Hashes are computed by canonicalising the JSON representation of a
    node's payload (with keys sorted and values normalised) and then
    applying the chosen hash algorithm.  An internal ``_hash_cache`` avoids
    recomputation for nodes that have already been hashed in this session.

    Attributes
    ----------
    hasher_id:
        Unique identifier for this hasher instance.
    hash_algorithm:
        Name of the hash algorithm to use (e.g. ``"sha256"`` or ``"md5"``).
    _hash_cache:
        Dict mapping node ids to previously computed hashes.
    include_trust_level:
        When ``True``, the node's trust level (if present) is included in
        the hash input.  Enabling this makes hashes trust-sensitive.
    """

    hasher_id: str
    hash_algorithm: str
    _hash_cache: dict[str, str]
    include_trust_level: bool

    def _make_hasher(self) -> Any:
        """Construct and return a fresh hasher object for ``self.hash_algorithm``."""
        algo = self.hash_algorithm.lower()
        if algo == "sha256":
            return hashlib.sha256()
        if algo == "sha1":
            return hashlib.sha1()
        if algo == "md5":
            return hashlib.md5()
        if algo == "sha512":
            return hashlib.sha512()
        return hashlib.sha256()

    def _canonicalise_payload(self, payload: dict[str, Any]) -> str:
        """Return a deterministic JSON string for *payload*.

        Keys are sorted recursively; float values are rounded to 12 decimal
        places to avoid floating-point noise.

        Parameters
        ----------
        payload:
            The node payload dict to canonicalise.
        """
        def _clean(obj: Any) -> Any:
            if isinstance(obj, float):
                return round(obj, 12)
            if isinstance(obj, dict):
                return {str(k): _clean(v) for k, v in sorted(obj.items())}
            if isinstance(obj, (list, tuple)):
                return [_clean(item) for item in obj]
            return obj

        return json.dumps(_clean(payload), sort_keys=True, separators=(",", ":"))

    def hash_node(self, node: Any) -> str:
        """Compute the canonical hash of a single IR node.

        The hash input is assembled from the node's kind string, its
        canonicalised payload, and (when ``include_trust_level`` is True)
        its trust level.  The result is a lowercase hex digest.

        Parameters
        ----------
        node:
            The IR node to hash.
        """
        node_id = str(getattr(node, "node_id", id(node)))
        if node_id in self._hash_cache:
            return self._hash_cache[node_id]

        kind = getattr(node, "kind", None)
        kind_str = str(kind.value) if hasattr(kind, "value") else str(kind)
        payload = getattr(node, "payload", {}) or {}
        canonical_payload = self._canonicalise_payload(payload)
        parts = [kind_str, canonical_payload]
        if self.include_trust_level:
            trust = getattr(node, "trust_level", None)
            if trust is not None:
                parts.append(str(trust))

        h = self._make_hasher()
        h.update(("\x00".join(parts)).encode("utf-8"))
        digest = h.hexdigest()
        self._hash_cache[node_id] = digest
        return digest

    def hash_layer(self, layer: Any) -> str:
        """Compute the canonical hash of a full IR layer.

        The layer hash is derived from the sorted list of node hashes
        plus the layer's own kind and id.  Sorting the node hashes ensures
        that layer hash is independent of node insertion order.

        Parameters
        ----------
        layer:
            The IR layer to hash.
        """
        layer_id = str(getattr(layer, "layer_id", id(layer)))
        layer_kind = getattr(layer, "kind", "")
        node_hashes = sorted(
            self.hash_node(node)
            for node in getattr(layer, "nodes", [])
        )
        combined = json.dumps(
            {"layer_id": layer_id, "kind": str(layer_kind), "nodes": node_hashes},
            sort_keys=True,
        )
        h = self._make_hasher()
        h.update(combined.encode("utf-8"))
        return h.hexdigest()

    def hash_payload(self, payload: dict) -> str:
        """Compute the hash of a raw payload dict.

        Useful for hashing partial or synthetic payloads outside of a full
        IR node context.

        Parameters
        ----------
        payload:
            The dict to hash.
        """
        canonical = self._canonicalise_payload(payload)
        h = self._make_hasher()
        h.update(canonical.encode("utf-8"))
        return h.hexdigest()

    def cache_key_for(self, node: Any, strategy: ReductionStrategy) -> str:
        """Construct the cache key for a (node, strategy) pair.

        The key encodes both the node's content (via its canonical hash) and
        the reduction strategy kind, so that results from different strategies
        are kept separate in the cache.

        Parameters
        ----------
        node:
            The IR node being normalised.
        strategy:
            The :class:`ReductionStrategy` being applied.
        """
        node_hash = self.hash_node(node)
        strategy_tag = str(strategy.strategy_kind)
        lazy_tag = "lazy" if strategy.is_lazy else "strict"
        return f"{node_hash}:{strategy_tag}:{lazy_tag}"

    def are_alpha_equivalent(self, node1: Any, node2: Any) -> bool:
        """Return ``True`` if *node1* and *node2* are alpha-equivalent.

        Alpha equivalence is checked by comparing the canonical hashes of
        both nodes after renaming all bound variables to a canonical form.
        The renaming is performed by traversing the payload and replacing
        lambda parameter names with positional names (``__v0``, ``__v1``,
        etc.) in depth-first order.

        Parameters
        ----------
        node1:
            The first node.
        node2:
            The second node.
        """
        canonical1 = _alpha_rename(node1)
        canonical2 = _alpha_rename(node2)
        hash1 = self.hash_payload(getattr(canonical1, "payload", {}) or {})
        hash2 = self.hash_payload(getattr(canonical2, "payload", {}) or {})
        return hash1 == hash2

    def batch_hash(self, nodes: list[Any]) -> dict[str, str]:
        """Hash multiple nodes and return a mapping from node id to hash.

        Nodes whose ids are already in ``_hash_cache`` are served from the
        cache; others are freshly computed and cached.

        Parameters
        ----------
        nodes:
            The list of IR nodes to hash.

        Returns
        -------
        dict[str, str]
            Mapping from ``str(node.node_id)`` to hex-digest hash string.
        """
        result: dict[str, str] = {}
        for node in nodes:
            node_id = str(getattr(node, "node_id", id(node)))
            result[node_id] = self.hash_node(node)
        return result


# ===================================================================== #
# Internal helpers (not part of the public API)                          #
# ===================================================================== #

def _get_children_any(node: Any) -> list[Any]:
    """Return the list of direct child nodes, handling varied node shapes."""
    children = getattr(node, "children", None)
    if isinstance(children, list):
        return children
    payload = getattr(node, "payload", {}) or {}
    child_list = payload.get("children", [])
    return child_list if isinstance(child_list, list) else []


def _node_with_payload(node: Any, new_payload: dict[str, Any]) -> Any:
    """Return a node-like object with the same kind but a new payload.

    If *node* supports ``_replace`` (named tuple) or ``__dataclass_fields__``
    (dataclass), those mechanisms are used.  Otherwise a plain dict is
    returned.

    Parameters
    ----------
    node:
        The original IR node.
    new_payload:
        The replacement payload dict.
    """
    if hasattr(node, "__dataclass_fields__"):
        import dataclasses
        return dataclasses.replace(node, payload=new_payload)
    if hasattr(node, "_replace"):
        return node._replace(payload=new_payload)
    return {"kind": getattr(node, "kind", "unknown"), "payload": new_payload,
            "node_id": getattr(node, "node_id", str(uuid.uuid4()))}


def _node_with_children(node: Any, new_children: list[Any]) -> Any:
    """Return a node-like object with a new children list.

    Parameters
    ----------
    node:
        The original IR node.
    new_children:
        The replacement children list.
    """
    if hasattr(node, "__dataclass_fields__"):
        import dataclasses
        payload = dict(getattr(node, "payload", {}) or {})
        payload["children"] = new_children
        return dataclasses.replace(node, payload=payload)
    if hasattr(node, "_replace"):
        payload = dict(getattr(node, "payload", {}) or {})
        payload["children"] = new_children
        return node._replace(payload=payload)
    payload = dict(getattr(node, "payload", {}) or {})
    payload["children"] = new_children
    return {"kind": getattr(node, "kind", "unknown"), "payload": payload,
            "node_id": getattr(node, "node_id", str(uuid.uuid4()))}


def _substitute(node: Any, var_name: str, replacement: Any) -> Any:
    """Substitute *replacement* for all free occurrences of *var_name* in *node*.

    Performs a recursive structural substitution.  Bound occurrences (inside
    lambda abstractions whose parameter matches *var_name*) are skipped.

    Parameters
    ----------
    node:
        The IR node tree to substitute within.
    var_name:
        The variable name to replace.
    replacement:
        The replacement node (or value) to splice in.
    """
    kind = str(getattr(node, "kind", "")) if not isinstance(node, str) else ""
    if isinstance(node, str):
        return replacement if node == var_name else node

    payload = dict(getattr(node, "payload", {}) or {})
    # If this is a lambda that binds var_name, stop descending
    if kind in ("lambda", "abstraction", "lam", "fn"):
        param = str(payload.get("param", payload.get("parameter", "")))
        if param == var_name:
            return node

    # Recurse into known payload sub-terms
    new_payload: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, str):
            new_payload[k] = replacement if v == var_name else v
        elif v is not None and hasattr(v, "payload"):
            new_payload[k] = _substitute(v, var_name, replacement)
        elif isinstance(v, list):
            new_payload[k] = [
                _substitute(item, var_name, replacement)
                if hasattr(item, "payload") else
                (replacement if item == var_name else item)
                for item in v
            ]
        else:
            new_payload[k] = v

    # Recurse into explicit children
    children = getattr(node, "children", None)
    if isinstance(children, list):
        new_children = [_substitute(c, var_name, replacement) for c in children]
        return _node_with_children(_node_with_payload(node, new_payload), new_children)

    return _node_with_payload(node, new_payload)


def _name_is_free(node: Any, name: str) -> bool:
    """Return ``True`` if *name* appears free anywhere in *node*.

    Respects lambda binding: a name is not free inside a lambda that binds
    the same name.

    Parameters
    ----------
    node:
        The IR node tree to search.
    name:
        The variable name to look for.
    """
    if isinstance(node, str):
        return node == name
    kind = str(getattr(node, "kind", ""))
    payload = getattr(node, "payload", {}) or {}
    if kind in ("lambda", "abstraction", "lam", "fn"):
        param = str(payload.get("param", payload.get("parameter", "")))
        if param == name:
            return False
    for v in payload.values():
        if isinstance(v, str):
            if v == name:
                return True
        elif hasattr(v, "payload"):
            if _name_is_free(v, name):
                return True
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item == name:
                    return True
                elif hasattr(item, "payload") and _name_is_free(item, name):
                    return True
    for child in _get_children_any(node):
        if _name_is_free(child, name):
            return True
    return False


def _alpha_rename(node: Any, counter: list[int] | None = None) -> Any:
    """Rename all bound variables in *node* to canonical positional names.

    Bound variable names are replaced with ``__v0``, ``__v1``, etc. in
    depth-first pre-order.  This normalisation is used to check alpha
    equivalence.

    Parameters
    ----------
    node:
        The IR node tree to rename.
    counter:
        Mutable single-element list used as a counter across recursive calls.
        Pass ``None`` to initialise a fresh counter at the root call.
    """
    if counter is None:
        counter = [0]
    if isinstance(node, str):
        return node
    kind = str(getattr(node, "kind", ""))
    payload = dict(getattr(node, "payload", {}) or {})
    if kind in ("lambda", "abstraction", "lam", "fn"):
        old_param = str(payload.get("param", payload.get("parameter", "__x")))
        new_param = f"__v{counter[0]}"
        counter[0] += 1
        body = payload.get("body")
        if body is not None:
            new_body = _substitute(body, old_param, new_param)
            new_body = _alpha_rename(new_body, counter)
            key = "param" if "param" in payload else "parameter"
            new_payload = dict(payload)
            new_payload[key] = new_param
            new_payload["body"] = new_body
            return _node_with_payload(node, new_payload)
    new_payload: dict[str, Any] = {}
    for k, v in payload.items():
        if hasattr(v, "payload"):
            new_payload[k] = _alpha_rename(v, counter)
        elif isinstance(v, list):
            new_payload[k] = [
                _alpha_rename(item, counter) if hasattr(item, "payload") else item
                for item in v
            ]
        else:
            new_payload[k] = v
    node = _node_with_payload(node, new_payload)
    children = getattr(node, "children", None)
    if isinstance(children, list):
        new_children = [_alpha_rename(c, counter) for c in children]
        node = _node_with_children(node, new_children)
    return node


# ===================================================================== #
# Module-level convenience functions                                      #
# ===================================================================== #

def compute_normal_form(node: Any, strategy_kind: Any = None) -> dict[str, Any]:
    """Compute the normal form of *node* under the given strategy kind.

    Creates a temporary :class:`ReductionStrategy` and :class:`NormalFormCache`
    scoped to this call.  The result is stored in the process-level
    ``_GLOBAL_NF_CACHE`` keyed by the canonical hash of the node + strategy.

    Parameters
    ----------
    node:
        The IR node to normalise.
    strategy_kind:
        A :class:`~jugeo.encodings.ir_stack.models.NormalFormKind` value or
        plain string.  Defaults to ``"full_normal"`` when ``None``.

    Returns
    -------
    dict
        A normal-form dict with keys ``"node_id"``, ``"reduced_payload"``,
        ``"strategy_kind"``, ``"steps"``, and ``"cached_at"``.
    """
    effective_kind = strategy_kind if strategy_kind is not None else "full_normal"
    strategy = ReductionStrategy(
        strategy_id=str(uuid.uuid4()),
        strategy_kind=effective_kind,
        max_steps=500,
        is_lazy=False,
        _step_count=0,
    )
    hasher = CanonicalHasher(
        hasher_id=str(uuid.uuid4()),
        hash_algorithm="sha256",
        _hash_cache={},
        include_trust_level=False,
    )
    key = hasher.cache_key_for(node, strategy)
    if key in _GLOBAL_NF_CACHE:
        return _GLOBAL_NF_CACHE[key]  # type: ignore[return-value]
    reduced = strategy.reduce_full(node)
    nf: dict[str, Any] = {
        "node_id": str(getattr(node, "node_id", str(uuid.uuid4()))),
        "reduced_payload": getattr(reduced, "payload", {}),
        "strategy_kind": str(effective_kind),
        "steps": strategy.step_count(),
        "cached_at": time.time(),
    }
    _GLOBAL_NF_CACHE[key] = nf
    return nf


def compare_normal_forms(nf1: Any, nf2: Any) -> int:
    """Compare two normal form objects and return -1, 0, or 1.

    Comparison is performed on the canonical JSON of the ``"reduced_payload"``
    fields.  If both payloads serialise to the same string, ``0`` is returned.
    Otherwise the result is ``-1`` (nf1 < nf2 lexicographically) or ``1``.

    Parameters
    ----------
    nf1:
        First normal form (dict with ``"reduced_payload"`` key, or object
        with ``.payload`` attribute).
    nf2:
        Second normal form.
    """
    def _extract_payload(nf: Any) -> str:
        if isinstance(nf, dict):
            p = nf.get("reduced_payload", nf.get("payload", {}))
        else:
            p = getattr(nf, "payload", {}) or {}
        return json.dumps(p, sort_keys=True, default=str)

    s1 = _extract_payload(nf1)
    s2 = _extract_payload(nf2)
    if s1 < s2:
        return -1
    if s1 > s2:
        return 1
    return 0


def cache_lookup(key: str) -> Any | None:
    """Look up *key* in the process-level normal form cache.

    Returns the cached normal-form object if present, or ``None`` on a miss.

    Parameters
    ----------
    key:
        The canonical cache key to look up.
    """
    return _GLOBAL_NF_CACHE.get(key)


def build_standard_rules() -> list[ReductionRule]:
    """Construct and return the standard beta and eta reduction rules.

    Beta rule: ``(λx. body) arg`` → ``body[arg/x]``
    Eta rule: ``λx. (f x)`` → ``f``  (when ``x`` not free in ``f``)

    Returns a list of two :class:`ReductionRule` objects with priorities 0
    (beta) and 1 (eta).

    Returns
    -------
    list[ReductionRule]
        The standard beta/eta rule set.
    """
    beta_rule = ReductionRule(
        rule_id=str(uuid.uuid4()),
        rule_name="beta",
        pattern={
            "kind": "application",
            "func": "?",
            "arg": "?",
        },
        replacement={
            "kind": "substituted_body",
            "$func": "$func",
            "$arg": "$arg",
        },
        conditions=[
            {"kind": "payload_key_exists", "key": "func"},
            {"kind": "payload_key_exists", "key": "arg"},
        ],
        priority=0,
    )
    eta_rule = ReductionRule(
        rule_id=str(uuid.uuid4()),
        rule_name="eta",
        pattern={
            "kind": "lambda",
            "param": "?",
            "body": "?",
        },
        replacement={
            "kind": "eta_reduced",
            "$param": "$param",
            "$body": "$body",
        },
        conditions=[
            {"kind": "not_free_in", "var": "$param", "subterm": "body"},
        ],
        priority=1,
    )
    return [beta_rule, eta_rule]


def check_confluence(nodes: list[Any], rules: list[ReductionRule]) -> bool:
    """Check whether *rules* form a confluent rewriting system on *nodes*.

    Creates a :class:`ConfluenceChecker` loaded with *rules*, then calls
    :meth:`~ConfluenceChecker.check_global_confluence` on all nodes.

    Parameters
    ----------
    nodes:
        The IR nodes to test confluence on.
    rules:
        The rewriting rules to check.

    Returns
    -------
    bool
        ``True`` if the rule set is confluent on the given nodes.
    """
    checker = ConfluenceChecker(
        checker_id=str(uuid.uuid4()),
        rules=list(rules),
        _proof_obligations=[],
        _confluence_cache={},
    )
    return checker.check_global_confluence(nodes)
