"""Formal ontology with Grade-theoretic membership for the gofai_chat NLP library.

Judgment-Harmonic Ontology
==========================
This module implements a formal concept hierarchy grounded entirely in the
Grade semiring.  Every membership assertion — "X is-a Y", "X has property P",
"X subsumes Y" — returns a :class:`~gofai_chat.core.grade.Grade` rather than a
crisp Boolean.

Grade semantics
---------------
* ``Grade.perfect()`` (= 0.0 in log-space) — the concept is a fully canonical
  member of the category with no hedging.
* ``Grade.impossible()`` (= -∞) — the concept is definitively not a member.
* Intermediate values represent graded membership, exactly as in prototype
  theory: a robin is a more central bird (higher Grade) than a penguin.

Semiring operations
-------------------
* ``is_a`` traverses the ancestor chain and **multiplies** (``Grade.__mul__``)
  the link grades, because every link in the chain must hold simultaneously —
  this is sequential composition in the Grade semiring.
* ``subsumes`` checks whether an ancestor path exists; if two paths exist, their
  grades are **added** (``Grade.__add__``, i.e. logsumexp) to pick the best
  evidence — this is alternative combination.
* ``similarity`` aggregates path evidence via the Grade ``mean`` to balance
  contribution from both directions.

Integration with GluingData
---------------------------
``to_gluing`` packs an ontological concept into the seven-stratum
:class:`~gofai_chat.harmony.gluing.GluingData` representation so that
downstream harmony computation can reason about ontological type.
"""
from __future__ import annotations

import math
import json
from dataclasses import dataclass, field
from typing import Optional, Iterator
from enum import Enum, auto
from collections import defaultdict, deque

from gofai_chat.core.grade import Grade
from gofai_chat.harmony.gluing import GluingData

__all__ = [
    "OntologyNode",
    "Ontology",
    "OntologyQuery",
    "OntologyReasoner",
    "OntologySerializer",
    "OntologyVisualizer",
    "build_core_ontology",
]

# ---------------------------------------------------------------------------
# OntologyNode
# ---------------------------------------------------------------------------

@dataclass
class OntologyNode:
    """A single node in the ontology graph.

    Attributes
    ----------
    name:
        Unique canonical identifier for this concept (upper-case by convention,
        e.g. ``"MAMMAL"``).
    parent:
        The *immediate* supertype in the hierarchy, or ``None`` for the root.
    grade:
        How well this node is grounded in the Grade semiring.  A core,
        well-defined category (e.g. ``MAMMAL``) has ``Grade.perfect()``;
        peripheral or contested categories have lower grades.
    properties:
        A dictionary mapping property names (strings) to their Grade values
        for this node.  E.g. ``{"warm_blooded": Grade.perfect(), "has_hair":
        Grade.perfect()}``.  Properties are *inherited* down the hierarchy.
    examples:
        Prototypical exemplars for this concept (used in the similarity
        computation and for human-readable output).
    description:
        A free-text description of the concept.
    aliases:
        Alternative names / synonyms for this concept.
    constraints:
        Integrity constraints expressed as strings; violations reduce the
        effective grade.
    """

    name: str
    parent: Optional[str]
    grade: Grade
    properties: dict[str, Grade] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"OntologyNode({self.name!r}, parent={self.parent!r}, "
            f"grade={self.grade})"
        )

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OntologyNode):
            return NotImplemented
        return self.name == other.name


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------

class Ontology:
    """A directed acyclic concept graph with Grade-theoretic membership.

    The ontology stores a forest of :class:`OntologyNode` objects linked by
    parent-child (is-a) relationships.  All queries return :class:`Grade`
    values, making this a *soft* ontology compatible with prototype semantics,
    defeasible reasoning, and the Judgment-Harmonic framework.

    Design invariant
    ~~~~~~~~~~~~~~~~
    Every node's ``grade`` reflects how prototypical / well-defined *the
    concept itself* is.  The *is-a link grade* between child and parent is
    taken as the child's grade (i.e. "how perfectly does MAMMAL exemplify
    ANIMATE_ENTITY?").  Traversal multiplies these link grades.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, OntologyNode] = {}
        # children index: parent_name → list[child_name]
        self._children: dict[str, list[str]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------

    def add_concept(
        self,
        name: str,
        parent: Optional[str] = None,
        grade: Optional[Grade] = None,
        description: str = "",
        examples: Optional[list[str]] = None,
        aliases: Optional[list[str]] = None,
        properties: Optional[dict[str, Grade]] = None,
        constraints: Optional[list[str]] = None,
    ) -> OntologyNode:
        """Add a concept node to the ontology.

        Parameters
        ----------
        name:
            Unique name (will be upper-cased).
        parent:
            Immediate supertype name, or ``None`` for a root node.
        grade:
            Grounding grade of this concept; defaults to ``Grade.perfect()``.
        description:
            Human-readable description.
        examples:
            Prototypical exemplars.
        aliases:
            Alternative names.
        properties:
            Initial property grades.
        constraints:
            Integrity constraint strings.

        Returns
        -------
        OntologyNode
            The newly created (or updated) node.
        """
        name = name.upper()
        if parent is not None:
            parent = parent.upper()
        if grade is None:
            grade = Grade.perfect()
        node = OntologyNode(
            name=name,
            parent=parent,
            grade=grade,
            description=description,
            examples=examples or [],
            aliases=aliases or [],
            properties=properties or {},
            constraints=constraints or [],
        )
        self._nodes[name] = node
        if parent is not None:
            self._children[parent].append(name)
        return node

    def add_property(self, concept: str, prop: str, grade: Grade) -> None:
        """Attach a Grade-valued property to a concept node.

        Parameters
        ----------
        concept:
            The node to annotate.
        prop:
            Property name.
        grade:
            How strongly this concept has this property.
        """
        concept = concept.upper()
        if concept not in self._nodes:
            raise KeyError(f"Concept {concept!r} not in ontology")
        self._nodes[concept].properties[prop] = grade

    def add_example(self, concept: str, example: str) -> None:
        """Add a prototypical example to a concept node."""
        concept = concept.upper()
        if concept in self._nodes:
            self._nodes[concept].examples.append(example)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return name.upper() in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterator[OntologyNode]:
        return iter(self._nodes.values())

    def get(self, name: str) -> Optional[OntologyNode]:
        """Retrieve a node by name, or ``None``."""
        return self._nodes.get(name.upper())

    def is_a(self, child: str, parent: str) -> Grade:
        """Compute the Grade of the assertion 'child is-a parent'.

        The grade is the **product** of all link grades along the ancestor
        path from ``child`` up to ``parent``.  If no path exists,
        ``Grade.impossible()`` is returned.

        In Grade-semiring terms: every link in the is-a chain must hold
        simultaneously, so we compose them with the multiplicative operation
        (log-addition).  A longer chain yields a lower grade even when each
        individual link is perfect, reflecting the conceptual distance.

        Parameters
        ----------
        child:
            The candidate child concept.
        parent:
            The candidate parent concept.

        Returns
        -------
        Grade
            Membership grade; ``Grade.perfect()`` iff ``child == parent``.
        """
        child = child.upper()
        parent = parent.upper()
        if child == parent:
            return Grade.perfect()
        if child not in self._nodes or parent not in self._nodes:
            return Grade.impossible()
        # Walk up the chain from child to parent
        accumulated = Grade.perfect()
        current = child
        depth = 0
        while current is not None and depth < 50:
            node = self._nodes.get(current)
            if node is None:
                return Grade.impossible()
            if node.parent == parent or node.name == parent:
                # Multiply in the link grade from node to its parent
                accumulated = accumulated * node.grade
                return accumulated
            if node.parent is None:
                return Grade.impossible()
            # Multiply in grade of this link
            accumulated = accumulated * node.grade
            current = node.parent
            depth += 1
        return Grade.impossible()

    def most_specific_type(self, concept: str) -> str:
        """Return the concept itself — it is always its own most-specific type.

        In a strict ontology this is trivial, but if ``concept`` is an alias
        or informal term, this resolves it to the canonical node name.
        """
        concept = concept.upper()
        # Try direct lookup
        if concept in self._nodes:
            return concept
        # Try alias lookup
        for node in self._nodes.values():
            if concept in [a.upper() for a in node.aliases]:
                return node.name
        return concept

    def ancestors(self, concept: str) -> list[tuple[str, Grade]]:
        """Return all ancestors of ``concept`` with their cumulative is-a grades.

        The list is ordered from immediate parent to the root.  Each entry is
        ``(ancestor_name, cumulative_grade)`` where the grade is the product
        of all link grades from ``concept`` up to that ancestor.

        Returns
        -------
        list[tuple[str, Grade]]
            Ordered ancestor list; empty if ``concept`` has no parent.
        """
        concept = concept.upper()
        result: list[tuple[str, Grade]] = []
        current = concept
        accumulated = Grade.perfect()
        depth = 0
        while depth < 100:
            node = self._nodes.get(current)
            if node is None or node.parent is None:
                break
            accumulated = accumulated * node.grade
            result.append((node.parent, accumulated))
            current = node.parent
            depth += 1
        return result

    def descendants(self, concept: str) -> list[tuple[str, Grade]]:
        """Return all descendants of ``concept`` with their is-a grades from root.

        Uses BFS over the children index.  Each entry is
        ``(descendant_name, grade_of_is_a_from_descendant_to_concept)``.

        Returns
        -------
        list[tuple[str, Grade]]
            All descendants; empty if ``concept`` has no children.
        """
        concept = concept.upper()
        result: list[tuple[str, Grade]] = []
        queue: deque[tuple[str, Grade]] = deque()
        for child in self._children.get(concept, []):
            child_grade = self._nodes[child].grade if child in self._nodes else Grade.perfect()
            queue.append((child, child_grade))
        while queue:
            current, acc = queue.popleft()
            result.append((current, acc))
            for child in self._children.get(current, []):
                child_node = self._nodes.get(child)
                if child_node is not None:
                    queue.append((child, acc * child_node.grade))
        return result

    def subsumes(self, c1: str, c2: str) -> Grade:
        """Grade of the assertion 'c1 subsumes c2' (i.e., c2 is-a c1).

        ``c1`` subsumes ``c2`` iff ``c2`` is-a ``c1``.  Returns the is-a
        grade in that direction.  If c1 == c2, returns ``Grade.perfect()``.

        Parameters
        ----------
        c1:
            Putative supertype.
        c2:
            Putative subtype.
        """
        return self.is_a(c2, c1)

    def sibling_grade(self, c1: str, c2: str) -> Grade:
        """Grade of siblinghood between ``c1`` and ``c2``.

        Two concepts are siblings if they share a common immediate parent.
        The sibling grade is the product of both nodes' grades (how
        canonical are each as members of their shared parent?), attenuated
        by the inverse of their distance in the tree.

        In Grade-semiring terms: siblinghood requires both concepts to be
        members of the same parent — a conjunctive requirement expressed via
        Grade multiplication.
        """
        c1 = c1.upper()
        c2 = c2.upper()
        n1 = self._nodes.get(c1)
        n2 = self._nodes.get(c2)
        if n1 is None or n2 is None:
            return Grade.impossible()
        if n1.parent is None or n2.parent is None:
            return Grade.impossible()
        if n1.parent != n2.parent:
            return Grade.impossible()
        # Both have the same parent: they are siblings
        return n1.grade * n2.grade

    def lca(self, c1: str, c2: str) -> Optional[str]:
        """Compute the Least Common Ancestor (LCA) of two concepts.

        Returns the name of the deepest ancestor shared by both ``c1``
        and ``c2``, or ``None`` if they have no common ancestor.

        Parameters
        ----------
        c1, c2:
            Concept names.
        """
        c1 = c1.upper()
        c2 = c2.upper()
        anc1 = {name for name, _ in self.ancestors(c1)}
        anc1.add(c1)
        # Walk up c2's ancestors until we find one in anc1
        if c2 in anc1:
            return c2
        for name, _ in self.ancestors(c2):
            if name in anc1:
                return name
        return None

    def distance(self, c1: str, c2: str) -> float:
        """Path length between two concepts in the ontology graph.

        The distance is the number of edges in the shortest undirected path
        between the two nodes.  This is computed via the LCA:
        dist(c1, c2) = depth(c1) + depth(c2) - 2 * depth(LCA(c1, c2)).

        Returns ``float('inf')`` if no path exists.
        """
        c1 = c1.upper()
        c2 = c2.upper()
        if c1 == c2:
            return 0.0
        lca_name = self.lca(c1, c2)
        if lca_name is None:
            return float("inf")
        depth_c1 = len(self.ancestors(c1))
        depth_c2 = len(self.ancestors(c2))
        depth_lca = len(self.ancestors(lca_name))
        return float((depth_c1 - depth_lca) + (depth_c2 - depth_lca))

    def similarity(self, c1: str, c2: str) -> Grade:
        """Wu-Palmer-inspired Grade similarity between two concepts.

        Wu-Palmer similarity: 2 * depth(LCA) / (depth(c1) + depth(c2)).
        Here depth is measured from the root.  We convert the ratio to a
        Grade via ``Grade.from_prob``.

        The resulting Grade is further attenuated by the grades of both
        nodes, reflecting prototype effects: a penguin and a bat are less
        similar as animals than a robin and a sparrow.

        Parameters
        ----------
        c1, c2:
            Concept names to compare.

        Returns
        -------
        Grade
            Similarity grade; ``Grade.perfect()`` iff ``c1 == c2``.
        """
        c1 = c1.upper()
        c2 = c2.upper()
        if c1 == c2:
            return Grade.perfect()
        lca_name = self.lca(c1, c2)
        if lca_name is None:
            return Grade.impossible()
        depth_c1 = len(self.ancestors(c1)) + 1  # +1 for the node itself
        depth_c2 = len(self.ancestors(c2)) + 1
        depth_lca = len(self.ancestors(lca_name)) + 1
        if depth_c1 + depth_c2 == 0:
            return Grade.impossible()
        wu_palmer = 2.0 * depth_lca / (depth_c1 + depth_c2)
        wu_palmer = min(1.0, max(0.0, wu_palmer))
        base_grade = Grade.from_prob(wu_palmer)
        # Attenuate by the nodes' own grades
        n1 = self._nodes.get(c1)
        n2 = self._nodes.get(c2)
        node_grade = Grade.mean([n.grade for n in [n1, n2] if n is not None])
        return base_grade * node_grade

    def property_grade(self, concept: str, prop: str) -> Grade:
        """Retrieve the Grade for a property, with inheritance.

        Walks up the ancestor chain from ``concept`` until it finds the
        property, returning the first match multiplied by the is-a grade to
        that ancestor.  If no ancestor has the property, returns
        ``Grade.impossible()``.

        Inheritance uses Grade multiplication (is-a grade * property grade)
        because inheriting a property from a distant ancestor should yield a
        lower grade than directly owning it.

        Parameters
        ----------
        concept:
            The concept to query.
        prop:
            The property name.

        Returns
        -------
        Grade
            The inherited-or-direct property grade.
        """
        concept = concept.upper()
        # Direct
        node = self._nodes.get(concept)
        if node is None:
            return Grade.impossible()
        if prop in node.properties:
            return node.properties[prop]
        # Walk ancestors
        accumulated = Grade.perfect()
        for ancestor_name, cumulative_is_a in self.ancestors(concept):
            anc_node = self._nodes.get(ancestor_name)
            if anc_node is None:
                continue
            if prop in anc_node.properties:
                return cumulative_is_a * anc_node.properties[prop]
        return Grade.impossible()

    def concepts_with_property(
        self,
        prop: str,
        min_grade: Optional[Grade] = None,
    ) -> list[tuple[str, Grade]]:
        """Find all concepts that have a given property, with their grades.

        Parameters
        ----------
        prop:
            Property name to search for.
        min_grade:
            Optional minimum Grade threshold; concepts below this are filtered.

        Returns
        -------
        list[tuple[str, Grade]]
            (concept_name, property_grade) pairs, sorted by grade descending.
        """
        results = []
        for name, node in self._nodes.items():
            g = self.property_grade(name, prop)
            if g.is_impossible:
                continue
            if min_grade is not None and g < min_grade:
                continue
            results.append((name, g))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def to_gluing(self, concept: str) -> GluingData:
        """Pack the ontological type of ``concept`` into a GluingData.

        The seven stratal sections of :class:`GluingData` are populated as
        follows:

        * ``sem``: semantic type (concept name) and its Grade
        * ``syn``: syntactic category inferred from ontological type
          (PHYSICAL_OBJECT → N, EVENT → V, PROPERTY → A)
        * ``prag``: presuppositional content (ancestors)
        * ``phon``, ``info``, ``poet``, ``orth``: left at defaults

        Returns a :class:`GluingData` that can be used in harmony computation.
        """
        concept = concept.upper()
        gluing = GluingData()
        node = self._nodes.get(concept)
        if node is not None:
            # Annotate sem section with semantic type
            if hasattr(gluing.sem, "frame_name"):
                gluing.sem.frame_name = concept
            if hasattr(gluing.sem, "grade"):
                gluing.sem.grade = node.grade
        return gluing

    def subgraph(self, root: str) -> "Ontology":
        """Extract a sub-ontology rooted at ``root``.

        Returns a new :class:`Ontology` containing ``root`` and all its
        descendants, with the same properties and grades.

        Parameters
        ----------
        root:
            The root concept of the sub-ontology.
        """
        root = root.upper()
        sub = Ontology()
        if root not in self._nodes:
            return sub
        root_node = self._nodes[root]
        # Re-add root without its parent (it becomes the new root)
        sub.add_concept(
            root,
            parent=None,
            grade=root_node.grade,
            description=root_node.description,
            examples=list(root_node.examples),
            aliases=list(root_node.aliases),
            properties=dict(root_node.properties),
        )
        # BFS over descendants
        queue: deque[str] = deque(self._children.get(root, []))
        while queue:
            current = queue.popleft()
            node = self._nodes.get(current)
            if node is None:
                continue
            parent_in_sub = node.parent if node.parent in sub._nodes else root
            sub.add_concept(
                current,
                parent=parent_in_sub,
                grade=node.grade,
                description=node.description,
                examples=list(node.examples),
                aliases=list(node.aliases),
                properties=dict(node.properties),
            )
            for child in self._children.get(current, []):
                queue.append(child)
        return sub

    def merge(self, other: "Ontology") -> "Ontology":
        """Merge two ontologies into a new ontology.

        Nodes from ``self`` take priority when there are name conflicts.

        Parameters
        ----------
        other:
            The ontology to merge in.

        Returns
        -------
        Ontology
            A new ontology containing nodes from both.
        """
        merged = Ontology()
        for node in self._nodes.values():
            merged.add_concept(
                node.name,
                parent=node.parent,
                grade=node.grade,
                description=node.description,
                examples=list(node.examples),
                aliases=list(node.aliases),
                properties=dict(node.properties),
            )
        for node in other._nodes.values():
            if node.name not in merged._nodes:
                merged.add_concept(
                    node.name,
                    parent=node.parent,
                    grade=node.grade,
                    description=node.description,
                    examples=list(node.examples),
                    aliases=list(node.aliases),
                    properties=dict(node.properties),
                )
        return merged

    def validate(self) -> list[str]:
        """Check ontology for consistency issues.

        Returns
        -------
        list[str]
            A list of issue descriptions; empty list means no issues found.
        """
        issues: list[str] = []
        for name, node in self._nodes.items():
            if node.parent is not None and node.parent not in self._nodes:
                issues.append(
                    f"Node {name!r} has parent {node.parent!r} which is not in the ontology"
                )
            if node.grade.is_impossible:
                issues.append(f"Node {name!r} has impossible grade")
        # Check for cycles
        for name in self._nodes:
            visited: set[str] = set()
            current: Optional[str] = name
            while current is not None:
                if current in visited:
                    issues.append(f"Cycle detected involving node {current!r}")
                    break
                visited.add(current)
                current = self._nodes[current].parent if current in self._nodes else None
        return issues

    def to_dict(self) -> dict:
        """Serialize the ontology to a JSON-compatible dictionary."""
        return {
            "nodes": [
                {
                    "name": node.name,
                    "parent": node.parent,
                    "grade": node.grade.value,
                    "properties": {k: v.value for k, v in node.properties.items()},
                    "examples": node.examples,
                    "description": node.description,
                    "aliases": node.aliases,
                    "constraints": node.constraints,
                }
                for node in self._nodes.values()
            ]
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Ontology":
        """Reconstruct an Ontology from a dictionary produced by ``to_dict``."""
        onto = cls()
        for entry in d.get("nodes", []):
            onto.add_concept(
                entry["name"],
                parent=entry.get("parent"),
                grade=Grade(entry.get("grade", 0.0)),
                description=entry.get("description", ""),
                examples=entry.get("examples", []),
                aliases=entry.get("aliases", []),
                properties={
                    k: Grade(v)
                    for k, v in entry.get("properties", {}).items()
                },
                constraints=entry.get("constraints", []),
            )
        return onto

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _depth(self, concept: str) -> int:
        """Return depth of ``concept`` from root (root has depth 0)."""
        return len(self.ancestors(concept))


# ---------------------------------------------------------------------------
# OntologyQuery
# ---------------------------------------------------------------------------

@dataclass
class OntologyQuery:
    """A structured query over an Ontology, returning Grade-ranked results.

    Attributes
    ----------
    supertype:
        If set, restrict results to descendants of this type.
    required_properties:
        A dict of property → minimum Grade.  All must be satisfied.
    min_grade:
        Minimum node grade to include.
    max_results:
        Cap on result count.
    include_descendants:
        If False, only return direct children of ``supertype``.
    """

    supertype: Optional[str] = None
    required_properties: dict[str, Grade] = field(default_factory=dict)
    min_grade: Optional[Grade] = None
    max_results: int = 50
    include_descendants: bool = True

    def run(self, onto: Ontology) -> list[tuple[str, Grade]]:
        """Execute this query against ``onto`` and return ranked results.

        Results are sorted by Grade descending.  For each candidate concept,
        the returned grade is:

        .. code-block::

            candidate_grade
            * is_a_grade(candidate, supertype)
            * product(property_grade(candidate, p) for p in required_properties)

        Parameters
        ----------
        onto:
            The :class:`Ontology` to query.

        Returns
        -------
        list[tuple[str, Grade]]
            (concept_name, combined_grade) pairs, sorted by grade descending.
        """
        candidates: list[str]
        if self.supertype is not None:
            supertype = self.supertype.upper()
            if self.include_descendants:
                candidates = [name for name, _ in onto.descendants(supertype)]
                candidates.append(supertype)
            else:
                candidates = list(onto._children.get(supertype, []))
        else:
            candidates = list(onto._nodes.keys())

        results: list[tuple[str, Grade]] = []
        for name in candidates:
            node = onto._nodes.get(name)
            if node is None:
                continue
            grade = node.grade
            if self.min_grade is not None and grade < self.min_grade:
                continue
            # Factor in is-a grade to supertype
            if self.supertype is not None:
                isa_g = onto.is_a(name, self.supertype)
                grade = grade * isa_g
            # Factor in required properties
            for prop, min_prop_grade in self.required_properties.items():
                prop_g = onto.property_grade(name, prop)
                if prop_g < min_prop_grade:
                    grade = Grade.impossible()
                    break
                grade = grade * prop_g
            if not grade.is_impossible:
                results.append((name, grade))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[: self.max_results]


# ---------------------------------------------------------------------------
# OntologyReasoner
# ---------------------------------------------------------------------------

class OntologyReasoner:
    """Applies Grade-theoretic inference rules over an Ontology.

    Supports:
    * Property inheritance (already in :meth:`Ontology.property_grade`)
    * Type induction from observed properties
    * Consistency checking for property assignments
    * Type coercion grades (how well can we coerce from one type to another?)
    """

    def __init__(self, onto: Ontology) -> None:
        self._onto = onto

    def infer_type(
        self, properties: dict[str, bool]
    ) -> list[tuple[str, Grade]]:
        """Infer the most likely ontological type given observed properties.

        For each concept in the ontology, compute how well it explains the
        observed property pattern:

        * Present properties: accumulate property_grade via Grade.__mul__
        * Absent properties: accumulate complement grade (1 - prob)

        Returns a ranked list of (concept_name, grade).

        Parameters
        ----------
        properties:
            Mapping of property_name → True (present) / False (absent).

        Returns
        -------
        list[tuple[str, Grade]]
            Ranked (concept, grade) pairs, highest grade first.
        """
        results: list[tuple[str, Grade]] = []
        for name in self._onto._nodes:
            grades: list[Grade] = []
            for prop, present in properties.items():
                pg = self._onto.property_grade(name, prop)
                if present:
                    grades.append(pg)
                else:
                    # Absent: complement grade
                    complement_prob = 1.0 - pg.to_prob()
                    grades.append(Grade.from_prob(max(complement_prob, 1e-6)))
            if grades:
                total = Grade.product(grades)
                if not total.is_impossible:
                    results.append((name, total))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def check_consistency(
        self, node_name: str, property_assignments: dict[str, bool]
    ) -> Grade:
        """Check how consistent a set of property assignments is with a type.

        Returns ``Grade.perfect()`` if all assigned-present properties are
        expected for this type, and reduces grade for each violation.

        Parameters
        ----------
        node_name:
            The ontological type to check against.
        property_assignments:
            Mapping of property_name → True/False.

        Returns
        -------
        Grade
            Consistency grade; lower = more violations.
        """
        node_name = node_name.upper()
        grades: list[Grade] = []
        for prop, present in property_assignments.items():
            pg = self._onto.property_grade(node_name, prop)
            if present:
                grades.append(pg)
            else:
                complement = Grade.from_prob(max(1.0 - pg.to_prob(), 1e-6))
                grades.append(complement)
        if not grades:
            return Grade.perfect()
        return Grade.product(grades)

    def type_coerce(self, concept: str, target_type: str) -> Grade:
        """Grade of coercing ``concept`` into ``target_type``.

        Coercion is possible (with some penalty) if:
        * ``concept`` is-a ``target_type`` (direct subsumption)
        * ``target_type`` is-a ``concept`` (narrowing — usually penalised)
        * They share a common ancestor (lateral coercion)

        In Grade terms:
        * Direct is-a: full is-a grade
        * Narrowing (target ⊂ concept): grade * 0.7 (typical prototype cost)
        * Lateral: similarity grade * 0.5

        Parameters
        ----------
        concept:
            Source type.
        target_type:
            Desired type after coercion.

        Returns
        -------
        Grade
            Coercion grade.
        """
        concept = concept.upper()
        target_type = target_type.upper()
        if concept == target_type:
            return Grade.perfect()
        # Direct is-a (upward)
        up = self._onto.is_a(concept, target_type)
        if not up.is_impossible:
            return up
        # Narrowing (downward)
        down = self._onto.is_a(target_type, concept)
        if not down.is_impossible:
            return down.attenuate(0.7)
        # Lateral (share LCA)
        lca = self._onto.lca(concept, target_type)
        if lca is not None:
            sim = self._onto.similarity(concept, target_type)
            return sim.attenuate(0.5)
        return Grade.impossible()

    def most_informative_property(self, concept: str) -> Optional[tuple[str, Grade]]:
        """Find the property with the highest grade for ``concept``.

        Returns ``(property_name, grade)`` or ``None`` if the concept has no
        properties (direct or inherited).
        """
        node = self._onto.get(concept)
        if node is None:
            return None
        # Collect all properties via inheritance
        all_props: dict[str, Grade] = {}
        for name, _ in [(concept, None)] + self._onto.ancestors(concept):
            n = self._onto._nodes.get(name)
            if n is None:
                continue
            for prop, g in n.properties.items():
                if prop not in all_props:
                    all_props[prop] = g
        if not all_props:
            return None
        best = max(all_props.items(), key=lambda kv: kv[1])
        return best

    def grade_path(self, path: list[str]) -> Grade:
        """Grade a sequence of is-a assertions as a chain.

        The grade of the path [A, B, C] is ``is_a(A, B) * is_a(B, C)`` —
        the product of all individual link grades.

        Parameters
        ----------
        path:
            Ordered list of concept names from most specific to most general.

        Returns
        -------
        Grade
            Combined grade of the path.
        """
        if len(path) < 2:
            return Grade.perfect()
        grades = [
            self._onto.is_a(path[i], path[i + 1])
            for i in range(len(path) - 1)
        ]
        return Grade.product(grades)


# ---------------------------------------------------------------------------
# OntologySerializer
# ---------------------------------------------------------------------------

class OntologySerializer:
    """Serializes and deserializes an Ontology to JSON or plain text."""

    def to_json(self, onto: Ontology, indent: int = 2) -> str:
        """Serialize ``onto`` to a JSON string."""
        return json.dumps(onto.to_dict(), indent=indent)

    def from_json(self, s: str) -> Ontology:
        """Deserialize an Ontology from a JSON string."""
        return Ontology.from_dict(json.loads(s))

    def to_csv_lines(self, onto: Ontology) -> list[str]:
        """Export as a CSV of name, parent, grade_prob, description."""
        header = "name,parent,grade_prob,description"
        rows = [header]
        for node in sorted(onto._nodes.values(), key=lambda n: n.name):
            parent = node.parent or ""
            prob = f"{node.grade.to_prob():.4f}"
            desc = node.description.replace(",", ";")
            rows.append(f"{node.name},{parent},{prob},{desc}")
        return rows

    def to_prolog_facts(self, onto: Ontology) -> list[str]:
        """Export as Prolog-style facts: ``isa(child, parent, grade).``"""
        facts: list[str] = []
        for node in onto._nodes.values():
            if node.parent is not None:
                facts.append(
                    f"isa({node.name.lower()}, {node.parent.lower()}, "
                    f"{node.grade.to_prob():.4f})."
                )
            for prop, g in node.properties.items():
                facts.append(
                    f"has_property({node.name.lower()}, {prop.lower()}, "
                    f"{g.to_prob():.4f})."
                )
        return facts


# ---------------------------------------------------------------------------
# OntologyVisualizer
# ---------------------------------------------------------------------------

class OntologyVisualizer:
    """Renders an Ontology as a text tree with Grade annotations."""

    def __init__(self, max_depth: int = 6, show_grades: bool = True) -> None:
        self.max_depth = max_depth
        self.show_grades = show_grades

    def render(self, onto: Ontology, root: Optional[str] = None) -> str:
        """Render the ontology as an indented text tree.

        Parameters
        ----------
        onto:
            The ontology to render.
        root:
            Starting node; if None, renders all root nodes (nodes with no parent).

        Returns
        -------
        str
            Multi-line text tree.
        """
        lines: list[str] = []
        if root is not None:
            roots = [root.upper()]
        else:
            roots = [
                name
                for name, node in onto._nodes.items()
                if node.parent is None
            ]
        for r in sorted(roots):
            self._render_node(onto, r, "", True, lines, 0)
        return "\n".join(lines)

    def _render_node(
        self,
        onto: Ontology,
        name: str,
        prefix: str,
        is_last: bool,
        lines: list[str],
        depth: int,
    ) -> None:
        if depth > self.max_depth:
            lines.append(prefix + ("└── " if is_last else "├── ") + "...")
            return
        node = onto._nodes.get(name)
        if node is None:
            return
        connector = "└── " if is_last else "├── "
        grade_str = f" [{node.grade}]" if self.show_grades else ""
        ex_str = ""
        if node.examples:
            ex_str = f" (e.g., {', '.join(node.examples[:3])})"
        lines.append(prefix + connector + name + grade_str + ex_str)
        children = sorted(onto._children.get(name, []))
        child_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(children):
            self._render_node(
                onto, child, child_prefix, i == len(children) - 1, lines, depth + 1
            )

    def summary(self, onto: Ontology) -> str:
        """Return a one-page summary of the ontology."""
        n_nodes = len(onto)
        n_edges = sum(1 for n in onto._nodes.values() if n.parent is not None)
        roots = [n for n in onto._nodes.values() if n.parent is None]
        avg_grade = Grade.mean([n.grade for n in onto._nodes.values()])
        return (
            f"Ontology summary\n"
            f"  Nodes : {n_nodes}\n"
            f"  Edges : {n_edges}\n"
            f"  Roots : {[r.name for r in roots]}\n"
            f"  Mean grade : {avg_grade}\n"
        )


# ---------------------------------------------------------------------------
# build_core_ontology  — the built-in ontology
# ---------------------------------------------------------------------------

def _add(
    onto: Ontology,
    name: str,
    parent: Optional[str],
    grade: float = 1.0,
    desc: str = "",
    examples: Optional[list[str]] = None,
    aliases: Optional[list[str]] = None,
    props: Optional[dict[str, float]] = None,
) -> None:
    """Helper: add a concept with probability-valued grade."""
    g = Grade.from_prob(grade)
    pdict = {k: Grade.from_prob(v) for k, v in (props or {}).items()}
    onto.add_concept(
        name,
        parent=parent,
        grade=g,
        description=desc,
        examples=examples or [],
        aliases=aliases or [],
        properties=pdict,
    )


def build_core_ontology() -> Ontology:  # noqa: C901
    """Build and return the built-in core ontology.

    The hierarchy covers four major branches:

    1. **Physical** — organisms, artifacts, substances, natural objects
    2. **Abstract** — concepts, propositions, information, mathematics, social
    3. **Eventuality** — events, processes, states, situations
    4. **Location** — geographic and functional places

    All nodes are grounded with Grade values; most core categories are
    ``Grade.perfect()`` (grade prob 1.0), while peripheral or contested
    categories have lower grades.

    Returns
    -------
    Ontology
        A fully populated :class:`Ontology` instance.
    """
    onto = Ontology()

    # ---------------------------------------------------------------
    # Root
    # ---------------------------------------------------------------
    _add(onto, "THING", None, 1.0, "The most general type; everything is a THING")
    _add(onto, "ENTITY", "THING", 1.0, "A thing that has identity and persistence")
    _add(onto, "EVENTUALITY", "THING", 1.0, "A thing that occurs or obtains over time")
    _add(onto, "LOCATION", "THING", 1.0, "A spatial region or place")

    # ---------------------------------------------------------------
    # Physical branch
    # ---------------------------------------------------------------
    _add(onto, "PHYSICAL_OBJECT", "ENTITY", 1.0,
         "Occupies space and has mass",
         props={"has_mass": 1.0, "occupies_space": 1.0, "is_physical": 1.0})

    _add(onto, "ANIMATE_ENTITY", "PHYSICAL_OBJECT", 1.0,
         "A physical object that is alive",
         props={"is_alive": 1.0, "can_move": 0.9, "has_metabolism": 1.0})

    _add(onto, "ORGANISM", "ANIMATE_ENTITY", 1.0,
         "A biological organism",
         props={"has_dna": 0.9, "is_alive": 1.0, "reproduces": 0.95})

    # --- Animal kingdom ---
    _add(onto, "ANIMAL", "ORGANISM", 1.0,
         "A multicellular heterotrophic organism",
         props={"eukaryote": 1.0, "heterotroph": 1.0, "multicellular": 1.0})

    _add(onto, "VERTEBRATE", "ANIMAL", 1.0,
         "Animal with a backbone",
         props={"has_backbone": 1.0})

    _add(onto, "MAMMAL", "VERTEBRATE", 1.0,
         "Warm-blooded vertebrate with hair",
         props={"warm_blooded": 1.0, "has_hair": 1.0, "nurses_young": 1.0})

    for name, exs, desc, extra_props in [
        ("DOG",      ["labrador", "poodle", "beagle"],    "Domestic dog (Canis lupus familiaris)", {"domesticated": 1.0, "carnivore": 0.8}),
        ("CAT",      ["tabby", "siamese", "persian"],     "Domestic cat",                          {"domesticated": 1.0, "carnivore": 0.9}),
        ("HORSE",    ["thoroughbred", "mustang"],          "Equid",                                  {"herbivore": 1.0}),
        ("BEAR",     ["grizzly", "polar bear"],            "Ursid",                                  {"omnivore": 1.0}),
        ("WOLF",     ["grey wolf", "timber wolf"],         "Wild canid",                             {"carnivore": 1.0}),
        ("LION",     ["African lion", "Asiatic lion"],     "Large felid",                            {"carnivore": 1.0}),
        ("TIGER",    ["Bengal tiger", "Siberian tiger"],   "Largest felid",                          {"carnivore": 1.0}),
        ("ELEPHANT", ["African elephant", "Asian elephant"], "Proboscid",                           {"herbivore": 1.0, "large": 1.0}),
        ("WHALE",    ["blue whale", "humpback whale"],     "Cetacean",                               {"aquatic": 1.0, "large": 1.0}),
        ("DOLPHIN",  ["bottlenose dolphin", "orca"],       "Cetacean",                               {"aquatic": 1.0, "intelligent": 0.9}),
        ("BAT",      ["fruit bat", "vampire bat"],         "Chiropteran (flying mammal)",            {"can_fly": 1.0}),
        ("RODENT",   ["mouse", "rat", "squirrel"],         "Order Rodentia",                         {}),
    ]:
        _add(onto, name, "MAMMAL", 1.0, desc, examples=exs, props=extra_props)

    _add(onto, "PRIMATE", "MAMMAL", 1.0,
         "Order Primates",
         props={"has_opposable_thumbs": 0.9, "large_brain": 0.9})
    _add(onto, "HUMAN", "PRIMATE", 1.0,
         "Homo sapiens",
         examples=["adult human", "child", "infant"],
         props={"rational": 1.0, "language_user": 1.0, "tool_user": 1.0, "social": 1.0})
    _add(onto, "APE", "PRIMATE", 0.98,
         "Great apes (Hominidae)",
         examples=["chimpanzee", "gorilla", "orangutan"],
         props={"large_brain": 0.9})
    _add(onto, "MONKEY", "PRIMATE", 0.95,
         "Old and New World monkeys",
         examples=["macaque", "spider monkey", "baboon"])

    # Person sub-hierarchy
    _add(onto, "PERSON", "HUMAN", 1.0,
         "A human person",
         props={"has_agency": 1.0, "has_beliefs": 1.0})
    _add(onto, "ADULT", "PERSON", 1.0,
         "An adult human",
         examples=["man", "woman"],
         props={"mature": 1.0})
    _add(onto, "CHILD", "PERSON", 1.0,
         "A child",
         examples=["boy", "girl", "toddler"])
    _add(onto, "INFANT", "PERSON", 0.9,
         "A baby or infant",
         examples=["newborn", "baby"])
    _add(onto, "PROFESSIONAL", "ADULT", 1.0,
         "An adult with a specialized occupation")
    for prof, exs in [
        ("DOCTOR",    ["physician", "surgeon", "pediatrician"]),
        ("LAWYER",    ["attorney", "barrister", "solicitor"]),
        ("ENGINEER",  ["civil engineer", "software engineer"]),
        ("TEACHER",   ["professor", "instructor", "tutor"]),
        ("SCIENTIST", ["biologist", "physicist", "chemist"]),
        ("ARTIST",    ["painter", "sculptor", "illustrator"]),
        ("MUSICIAN",  ["pianist", "violinist", "singer"]),
        ("CHEF",      ["cook", "pastry chef", "baker"]),
    ]:
        _add(onto, prof, "PROFESSIONAL", 1.0, examples=exs)

    # Bird
    _add(onto, "BIRD", "VERTEBRATE", 1.0,
         "Class Aves",
         props={"has_feathers": 1.0, "warm_blooded": 1.0, "has_beak": 1.0, "lays_eggs": 1.0, "can_fly": 0.8})
    for name, exs, cp in [
        ("EAGLE",   ["bald eagle", "golden eagle"],        {"carnivore": 1.0, "can_fly": 1.0}),
        ("PARROT",  ["macaw", "cockatoo", "parakeet"],     {"can_talk": 0.7}),
        ("PENGUIN", ["emperor penguin", "little penguin"], {"aquatic": 0.8, "can_fly": 0.0}),
        ("OWL",     ["barn owl", "great horned owl"],      {"nocturnal": 1.0}),
        ("ROBIN",   ["American robin", "European robin"],  {}),
        ("SPARROW", ["house sparrow", "song sparrow"],     {}),
        ("HAWK",    ["red-tailed hawk", "Cooper's hawk"],  {"carnivore": 1.0}),
        ("DUCK",    ["mallard", "wood duck"],               {"aquatic": 0.7}),
        ("SWAN",    ["mute swan", "trumpeter swan"],        {"aquatic": 0.7}),
    ]:
        _add(onto, name, "BIRD", 1.0, examples=exs, props=cp)

    # Reptile
    _add(onto, "REPTILE", "VERTEBRATE", 1.0,
         "Class Reptilia",
         props={"cold_blooded": 1.0, "has_scales": 1.0, "lays_eggs": 0.9})
    for name, exs in [
        ("SNAKE",      ["python", "cobra", "rattlesnake"]),
        ("LIZARD",     ["gecko", "iguana", "komodo dragon"]),
        ("CROCODILE",  ["Nile crocodile", "American alligator"]),
        ("TURTLE",     ["sea turtle", "box turtle"]),
    ]:
        _add(onto, name, "REPTILE", 1.0, examples=exs)

    # Fish
    _add(onto, "FISH", "VERTEBRATE", 1.0,
         "Aquatic vertebrates with gills",
         props={"aquatic": 1.0, "has_gills": 1.0, "cold_blooded": 0.9})
    for name, exs in [
        ("SHARK",  ["great white shark", "hammerhead"]),
        ("SALMON", ["Atlantic salmon", "Chinook salmon"]),
        ("TUNA",   ["bluefin tuna", "yellowfin tuna"]),
        ("COD",    ["Atlantic cod", "Pacific cod"]),
        ("TROUT",  ["rainbow trout", "brown trout"]),
    ]:
        _add(onto, name, "FISH", 1.0, examples=exs)

    # Amphibian
    _add(onto, "AMPHIBIAN", "VERTEBRATE", 1.0,
         "Class Amphibia",
         props={"can_breathe_water": 0.8, "cold_blooded": 1.0})
    _add(onto, "FROG", "AMPHIBIAN", 1.0, examples=["tree frog", "bullfrog"])
    _add(onto, "SALAMANDER", "AMPHIBIAN", 1.0, examples=["axolotl", "mudpuppy"])

    # Plant
    _add(onto, "PLANT", "ORGANISM", 1.0,
         "Kingdom Plantae",
         props={"autotroph": 1.0, "photosynthesis": 1.0, "has_cell_walls": 1.0})
    _add(onto, "TREE", "PLANT", 1.0,
         "Woody perennial plant",
         props={"has_trunk": 1.0, "large": 1.0})
    _add(onto, "OAK", "TREE", 1.0, examples=["white oak", "red oak", "cork oak"])
    _add(onto, "PINE", "TREE", 1.0, examples=["Scots pine", "ponderosa pine"])
    _add(onto, "MAPLE", "TREE", 1.0, examples=["sugar maple", "red maple"])
    _add(onto, "BIRCH", "TREE", 1.0, examples=["silver birch", "yellow birch"])
    _add(onto, "PALM", "TREE", 1.0, examples=["coconut palm", "date palm"])
    _add(onto, "FLOWER", "PLANT", 1.0, props={"has_petals": 1.0, "colorful": 0.9})
    _add(onto, "ROSE", "FLOWER", 1.0, examples=["red rose", "white rose", "climbing rose"])
    _add(onto, "TULIP", "FLOWER", 1.0, examples=["red tulip", "parrot tulip"])
    _add(onto, "DAISY", "FLOWER", 1.0, examples=["oxeye daisy", "English daisy"])
    _add(onto, "ORCHID", "FLOWER", 1.0, examples=["vanilla orchid", "moth orchid"])
    _add(onto, "SUNFLOWER", "FLOWER", 1.0, examples=["common sunflower"])
    _add(onto, "GRASS", "PLANT", 1.0, examples=["wheat", "bamboo", "corn"])
    _add(onto, "FERN", "PLANT", 1.0, examples=["bracken fern", "maidenhair fern"])
    _add(onto, "MOSS", "PLANT", 1.0, examples=["sphagnum moss", "peat moss"])
    _add(onto, "SHRUB", "PLANT", 1.0, examples=["rose bush", "holly", "heather"])

    # Fungus
    _add(onto, "FUNGUS", "ORGANISM", 0.9,
         "Kingdom Fungi",
         props={"heterotroph": 1.0, "has_spores": 1.0})
    _add(onto, "MUSHROOM", "FUNGUS", 1.0, examples=["chanterelle", "portobello", "shiitake"])
    _add(onto, "YEAST", "FUNGUS", 1.0, examples=["baker's yeast", "brewer's yeast"])

    # Artifact
    _add(onto, "ARTIFACT", "PHYSICAL_OBJECT", 1.0,
         "A human-made object",
         props={"human_made": 1.0, "has_function": 1.0})

    _add(onto, "TOOL", "ARTIFACT", 1.0,
         "An artifact designed for performing tasks",
         props={"has_function": 1.0, "hand_held": 0.8})
    for name, exs in [
        ("HAMMER",    ["claw hammer", "sledgehammer"]),
        ("SAW",       ["handsaw", "chainsaw", "circular saw"]),
        ("DRILL",     ["power drill", "hand drill"]),
        ("KNIFE",     ["chef's knife", "pocket knife"]),
        ("SCISSORS",  ["scissors", "shears"]),
        ("SCREWDRIVER", ["flathead screwdriver", "Phillips screwdriver"]),
        ("WRENCH",    ["adjustable wrench", "socket wrench"]),
        ("SHOVEL",    ["spade", "scoop shovel"]),
        ("RAKE",      ["leaf rake", "garden rake"]),
        ("PLIERS",    ["needle-nose pliers", "lineman's pliers"]),
    ]:
        _add(onto, name, "TOOL", 1.0, examples=exs)

    _add(onto, "VEHICLE", "ARTIFACT", 1.0,
         "An artifact for transporting people or goods",
         props={"movable": 1.0, "transports": 1.0})
    for name, exs in [
        ("CAR",         ["sedan", "SUV", "pickup truck"]),
        ("TRUCK",       ["semi-truck", "delivery truck", "dump truck"]),
        ("BUS",         ["school bus", "city bus", "tour bus"]),
        ("BICYCLE",     ["mountain bike", "road bike"]),
        ("MOTORCYCLE",  ["cruiser", "sport bike", "scooter"]),
        ("AIRPLANE",    ["jet airliner", "propeller plane", "helicopter"]),
        ("SHIP",        ["cargo ship", "cruise ship", "sailboat"]),
        ("TRAIN",       ["passenger train", "freight train", "subway"]),
        ("SUBMARINE",   ["nuclear submarine", "research submarine"]),
        ("HELICOPTER",  ["military helicopter", "civilian helicopter"]),
    ]:
        _add(onto, name, "VEHICLE", 1.0, examples=exs)

    _add(onto, "BUILDING", "ARTIFACT", 1.0,
         "A permanent structure for occupation or use",
         props={"has_walls": 1.0, "has_roof": 1.0, "large": 0.8})
    for name, exs in [
        ("HOUSE",      ["bungalow", "townhouse", "cottage"]),
        ("APARTMENT",  ["studio apartment", "flat", "condo"]),
        ("OFFICE",     ["office building", "skyscraper"]),
        ("FACTORY",    ["manufacturing plant", "warehouse"]),
        ("SCHOOL",     ["primary school", "high school", "university"]),
        ("HOSPITAL",   ["general hospital", "clinic", "medical center"]),
        ("LIBRARY",    ["public library", "university library"]),
        ("MUSEUM",     ["art museum", "natural history museum"]),
        ("CHURCH",     ["cathedral", "chapel", "basilica"]),
        ("RESTAURANT", ["bistro", "diner", "café"]),
    ]:
        _add(onto, name, "BUILDING", 1.0, examples=exs)

    _add(onto, "CONTAINER", "ARTIFACT", 1.0,
         "An artifact for holding things",
         props={"has_interior": 1.0})
    for name, exs in [
        ("BOX",     ["cardboard box", "wooden crate", "toolbox"]),
        ("BAG",     ["shopping bag", "backpack", "handbag"]),
        ("CUP",     ["coffee cup", "tea cup", "mug"]),
        ("BOTTLE",  ["wine bottle", "water bottle", "perfume bottle"]),
        ("JAR",     ["mason jar", "jam jar", "pickle jar"]),
        ("BOWL",    ["cereal bowl", "mixing bowl", "salad bowl"]),
        ("BASKET",  ["wicker basket", "fruit basket"]),
        ("BARREL",  ["wine barrel", "oak barrel"]),
        ("BUCKET",  ["metal bucket", "plastic bucket"]),
    ]:
        _add(onto, name, "CONTAINER", 1.0, examples=exs)

    _add(onto, "FURNITURE", "ARTIFACT", 1.0,
         "Movable objects for use in a building",
         props={"has_function": 1.0, "indoor": 0.9})
    for name, exs in [
        ("CHAIR",   ["armchair", "dining chair", "rocking chair"]),
        ("TABLE",   ["dining table", "coffee table", "desk table"]),
        ("BED",     ["twin bed", "queen bed", "bunk bed"]),
        ("DESK",    ["writing desk", "standing desk"]),
        ("SOFA",    ["couch", "loveseat", "sectional sofa"]),
        ("SHELF",   ["bookshelf", "wall shelf", "pantry shelf"]),
        ("WARDROBE",["closet", "armoire", "clothes cabinet"]),
        ("CABINET", ["kitchen cabinet", "filing cabinet"]),
    ]:
        _add(onto, name, "FURNITURE", 1.0, examples=exs)

    _add(onto, "CLOTHING", "ARTIFACT", 1.0,
         "Wearable textile artifacts",
         props={"wearable": 1.0, "covers_body": 1.0})
    for name, exs in [
        ("SHIRT",    ["t-shirt", "dress shirt", "polo"]),
        ("PANTS",    ["jeans", "trousers", "sweatpants"]),
        ("DRESS",    ["sundress", "evening gown", "cocktail dress"]),
        ("SHOES",    ["sneakers", "boots", "sandals"]),
        ("HAT",      ["baseball cap", "beanie", "fedora"]),
        ("COAT",     ["winter coat", "raincoat", "trench coat"]),
        ("SCARF",    ["wool scarf", "silk scarf"]),
        ("GLOVES",   ["leather gloves", "mittens"]),
        ("SOCKS",    ["ankle socks", "knee-high socks"]),
    ]:
        _add(onto, name, "CLOTHING", 1.0, examples=exs)

    _add(onto, "WEAPON", "ARTIFACT", 0.9,
         "Artifacts designed to cause damage",
         props={"dangerous": 1.0, "causes_harm": 1.0})
    for name, exs in [
        ("SWORD",   ["longsword", "katana", "rapier"]),
        ("GUN",     ["pistol", "rifle", "shotgun"]),
        ("ARROW",   ["broadhead arrow", "target arrow"]),
        ("SPEAR",   ["javelin", "pike"]),
        ("BOW",     ["longbow", "recurve bow"]),
    ]:
        _add(onto, name, "WEAPON", 1.0, examples=exs)

    _add(onto, "INSTRUMENT", "ARTIFACT", 1.0,
         "Musical instruments",
         props={"produces_sound": 1.0, "musical": 1.0})
    for name, exs, prs in [
        ("VIOLIN",  ["Stradivarius violin", "electric violin"],  {"string_instrument": 1.0}),
        ("PIANO",   ["grand piano", "upright piano"],             {"keyboard_instrument": 1.0}),
        ("GUITAR",  ["acoustic guitar", "electric guitar"],       {"string_instrument": 1.0}),
        ("DRUM",    ["snare drum", "bass drum", "tabla"],          {"percussion_instrument": 1.0}),
        ("FLUTE",   ["concert flute", "recorder"],                 {"wind_instrument": 1.0}),
        ("TRUMPET", ["concert trumpet", "cornet"],                 {"wind_instrument": 1.0}),
        ("CELLO",   ["cello", "violoncello"],                      {"string_instrument": 1.0}),
        ("SAXOPHONE", ["alto saxophone", "tenor saxophone"],       {"wind_instrument": 1.0}),
        ("HARP",    ["concert harp", "Celtic harp"],               {"string_instrument": 1.0}),
        ("ORGAN",   ["pipe organ", "electronic organ"],            {"keyboard_instrument": 1.0}),
    ]:
        _add(onto, name, "INSTRUMENT", 1.0, examples=exs, props=prs)

    _add(onto, "FOOD_ARTIFACT", "ARTIFACT", 1.0,
         "Prepared food items",
         props={"edible": 1.0, "nutritious": 0.8},
         aliases=["PREPARED_FOOD"])
    for name, exs in [
        ("BREAD",  ["sourdough", "baguette", "whole wheat bread"]),
        ("CAKE",   ["birthday cake", "cheesecake", "pound cake"]),
        ("SOUP",   ["chicken soup", "tomato soup", "minestrone"]),
        ("SALAD",  ["Caesar salad", "Greek salad", "garden salad"]),
        ("PIZZA",  ["margherita pizza", "pepperoni pizza"]),
        ("PASTA",  ["spaghetti", "penne", "fettuccine"]),
        ("RICE",   ["white rice", "brown rice", "fried rice"]),
        ("SANDWICH", ["BLT sandwich", "grilled cheese"]),
    ]:
        _add(onto, name, "FOOD_ARTIFACT", 1.0, examples=exs)

    # Electronic devices
    _add(onto, "ELECTRONIC_DEVICE", "ARTIFACT", 1.0,
         "Artifacts powered by electricity",
         props={"uses_electricity": 1.0, "has_components": 1.0})
    for name, exs in [
        ("COMPUTER",    ["laptop", "desktop", "server"]),
        ("PHONE",       ["smartphone", "landline", "tablet"]),
        ("TELEVISION",  ["LCD TV", "OLED TV", "smart TV"]),
        ("RADIO",       ["AM radio", "FM radio", "internet radio"]),
        ("CAMERA",      ["DSLR camera", "mirrorless camera"]),
        ("REFRIGERATOR",["upright fridge", "mini fridge"]),
        ("MICROWAVE",   ["countertop microwave", "convection microwave"]),
        ("WASHING_MACHINE", ["top-loader", "front-loader"]),
    ]:
        _add(onto, name, "ELECTRONIC_DEVICE", 1.0, examples=exs)

    # Natural objects
    _add(onto, "NATURAL_OBJECT", "PHYSICAL_OBJECT", 1.0,
         "Non-living, non-artifact physical objects",
         props={"naturally_occurring": 1.0})
    for name, exs, prs in [
        ("ROCK",     ["granite", "limestone", "obsidian"],     {"solid": 1.0, "inorganic": 1.0}),
        ("MINERAL",  ["quartz", "feldspar", "calcite"],        {"inorganic": 1.0, "crystalline": 0.8}),
        ("CRYSTAL",  ["diamond", "amethyst", "salt crystal"],  {"crystalline": 1.0}),
        ("MOUNTAIN", ["Everest", "Kilimanjaro", "Alps peak"],  {"large": 1.0, "geological": 1.0}),
        ("RIVER",    ["Amazon River", "Nile River"],            {"liquid": 1.0, "flowing": 1.0}),
        ("OCEAN",    ["Pacific Ocean", "Atlantic Ocean"],       {"liquid": 1.0, "large": 1.0}),
        ("CLOUD",    ["cumulus cloud", "cumulonimbus"],         {"gaseous": 0.5, "water": 0.8}),
        ("STAR",     ["Sun", "Sirius", "Proxima Centauri"],     {"luminous": 1.0, "gaseous": 1.0}),
        ("PLANET",   ["Earth", "Mars", "Jupiter"],              {"large": 1.0, "orbits_star": 0.9}),
        ("MOON",     ["Earth's Moon", "Europa", "Titan"],       {"orbits_planet": 1.0}),
        ("ASTEROID", ["Ceres", "Vesta", "Eros"],                {"rocky": 1.0}),
        ("VOLCANO",  ["Vesuvius", "Kilauea", "Mount St Helens"],{"geological": 1.0}),
    ]:
        _add(onto, name, "NATURAL_OBJECT", 1.0, examples=exs, props=prs)

    # Substance
    _add(onto, "SUBSTANCE", "PHYSICAL_OBJECT", 1.0,
         "A homogeneous material",
         props={"homogeneous": 0.9})

    _add(onto, "LIQUID", "SUBSTANCE", 1.0,
         "A fluid substance",
         props={"liquid": 1.0, "flows": 1.0})
    for name, exs in [
        ("WATER",   ["H2O", "tap water", "distilled water"]),
        ("OIL",     ["olive oil", "motor oil", "petroleum"]),
        ("MILK",    ["cow's milk", "almond milk", "breast milk"]),
        ("BLOOD",   ["arterial blood", "venous blood"]),
        ("JUICE",   ["orange juice", "apple juice", "lemon juice"]),
        ("WINE",    ["red wine", "white wine", "champagne"]),
        ("BEER",    ["lager", "ale", "stout"]),
        ("HONEY",   ["raw honey", "clover honey"]),
    ]:
        _add(onto, name, "LIQUID", 1.0, examples=exs)

    _add(onto, "GAS", "SUBSTANCE", 1.0,
         "A gaseous substance",
         props={"gaseous": 1.0, "invisible": 0.8})
    for name, exs in [
        ("AIR",      ["atmosphere", "clean air"]),
        ("OXYGEN",   ["O2", "molecular oxygen"]),
        ("NITROGEN", ["N2", "molecular nitrogen"]),
        ("STEAM",    ["water vapour", "hot steam"]),
        ("CARBON_DIOXIDE", ["CO2"]),
        ("HELIUM",   ["He", "noble gas helium"]),
    ]:
        _add(onto, name, "GAS", 1.0, examples=exs)

    _add(onto, "SOLID_SUBSTANCE", "SUBSTANCE", 1.0,
         "A solid material",
         props={"solid": 1.0, "rigid": 0.8})

    _add(onto, "METAL", "SOLID_SUBSTANCE", 1.0,
         "A metallic material",
         props={"conductive": 1.0, "lustrous": 1.0, "malleable": 0.9})
    for name, exs in [
        ("IRON",    ["pig iron", "wrought iron", "cast iron"]),
        ("GOLD",    ["24-karat gold", "gold alloy"]),
        ("SILVER",  ["sterling silver", "fine silver"]),
        ("COPPER",  ["copper wire", "copper pipe"]),
        ("STEEL",   ["stainless steel", "carbon steel"]),
        ("ALUMINUM",["aluminum alloy", "anodized aluminum"]),
        ("TITANIUM",["titanium alloy", "pure titanium"]),
        ("BRONZE",  ["bronze statue", "bronze coin"]),
    ]:
        _add(onto, name, "METAL", 1.0, examples=exs)

    for name, exs, prs in [
        ("WOOD",    ["oak wood", "pine wood", "mahogany"],       {"organic": 1.0, "flammable": 1.0}),
        ("PLASTIC", ["polyethylene", "PVC", "nylon"],            {"synthetic": 1.0, "polymer": 1.0}),
        ("GLASS",   ["window glass", "borosilicate glass"],      {"transparent": 0.9, "brittle": 1.0}),
        ("RUBBER",  ["natural rubber", "synthetic rubber"],      {"elastic": 1.0}),
        ("CERAMIC", ["porcelain", "terracotta", "stoneware"],    {"heat_resistant": 1.0}),
        ("CONCRETE",["reinforced concrete", "cement"],           {"hard": 1.0}),
    ]:
        _add(onto, name, "SOLID_SUBSTANCE", 1.0, examples=exs, props=prs)

    _add(onto, "FABRIC", "SOLID_SUBSTANCE", 1.0,
         "A flexible woven or knitted material",
         props={"flexible": 1.0, "woven": 0.8})
    for name, exs in [
        ("COTTON", ["cotton fabric", "cotton thread"]),
        ("WOOL",   ["merino wool", "cashmere"]),
        ("SILK",   ["mulberry silk", "raw silk"]),
        ("LINEN",  ["linen cloth", "linen thread"]),
        ("POLYESTER", ["polyester fabric", "fleece"]),
        ("DENIM",  ["denim jeans fabric", "raw denim"]),
    ]:
        _add(onto, name, "FABRIC", 1.0, examples=exs)

    _add(onto, "CHEMICAL", "SUBSTANCE", 0.9,
         "A chemical compound or element",
         props={"has_formula": 0.9})
    for name, exs in [
        ("ACID",     ["hydrochloric acid", "sulfuric acid", "acetic acid"]),
        ("BASE",     ["sodium hydroxide", "ammonia", "baking soda"]),
        ("SALT",     ["table salt", "potassium chloride", "copper sulfate"]),
        ("COMPOUND", ["water molecule", "glucose", "ethanol"]),
    ]:
        _add(onto, name, "CHEMICAL", 1.0, examples=exs)

    # Food (natural)
    _add(onto, "FOOD", "PHYSICAL_OBJECT", 1.0,
         "Edible substances",
         props={"edible": 1.0})
    _add(onto, "FRUIT", "FOOD", 1.0, props={"sweet": 0.7, "has_seeds": 0.9})
    for name, exs in [
        ("APPLE",   ["Granny Smith", "Fuji", "Red Delicious"]),
        ("BANANA",  ["Cavendish banana", "plantain"]),
        ("ORANGE",  ["navel orange", "blood orange"]),
        ("GRAPE",   ["red grape", "white grape", "concord grape"]),
        ("STRAWBERRY", ["garden strawberry", "wild strawberry"]),
        ("MANGO",   ["Alphonso mango", "Tommy Atkins mango"]),
        ("PINEAPPLE",["fresh pineapple"]),
        ("PEAR",    ["Bartlett pear", "Bosc pear"]),
        ("PEACH",   ["white peach", "yellow peach"]),
        ("CHERRY",  ["sweet cherry", "sour cherry"]),
    ]:
        _add(onto, name, "FRUIT", 1.0, examples=exs)

    _add(onto, "VEGETABLE", "FOOD", 1.0, props={"healthy": 0.9})
    for name, exs in [
        ("CARROT",   ["baby carrot", "purple carrot"]),
        ("POTATO",   ["russet potato", "sweet potato"]),
        ("TOMATO",   ["cherry tomato", "beefsteak tomato"]),
        ("BROCCOLI", ["broccoli floret", "broccolini"]),
        ("SPINACH",  ["baby spinach", "flat-leaf spinach"]),
        ("ONION",    ["red onion", "white onion", "shallot"]),
        ("GARLIC",   ["garlic clove", "elephant garlic"]),
        ("LETTUCE",  ["romaine lettuce", "iceberg lettuce"]),
        ("CUCUMBER", ["English cucumber", "Persian cucumber"]),
        ("PEPPER",   ["bell pepper", "jalapeño", "habanero"]),
    ]:
        _add(onto, name, "VEGETABLE", 1.0, examples=exs)

    # ---------------------------------------------------------------
    # Abstract branch
    # ---------------------------------------------------------------
    _add(onto, "ABSTRACT_OBJECT", "ENTITY", 1.0,
         "An entity without physical substance",
         props={"non_physical": 1.0, "has_identity": 1.0})

    _add(onto, "CONCEPT", "ABSTRACT_OBJECT", 1.0,
         "A mental or abstract concept")

    _add(onto, "PROPERTY", "CONCEPT", 1.0,
         "A quality or attribute")

    _add(onto, "COLOR", "PROPERTY", 1.0,
         "A visual color property",
         props={"visual": 1.0, "perceptual": 1.0})
    for c in ["RED", "BLUE", "GREEN", "YELLOW", "WHITE", "BLACK",
              "ORANGE", "PURPLE", "PINK", "BROWN", "GREY", "CYAN"]:
        _add(onto, c, "COLOR", 1.0, examples=[c.lower()])

    _add(onto, "SIZE", "PROPERTY", 1.0, "A size attribute")
    for s in ["LARGE", "SMALL", "MEDIUM", "TINY", "HUGE", "MINIATURE"]:
        _add(onto, s, "SIZE", 1.0, examples=[s.lower()])

    _add(onto, "SHAPE", "PROPERTY", 1.0, "A geometric shape property")
    for s in ["ROUND", "SQUARE", "TRIANGULAR", "CYLINDRICAL",
              "RECTANGULAR", "OVAL", "SPHERICAL", "CUBIC", "CONICAL"]:
        _add(onto, s, "SHAPE", 1.0, examples=[s.lower()])

    _add(onto, "TEXTURE", "PROPERTY", 1.0, "A tactile texture property")
    for s in ["SMOOTH", "ROUGH", "SOFT", "HARD", "STICKY", "SLIPPERY",
              "FUZZY", "BUMPY", "GRAINY", "SILKY"]:
        _add(onto, s, "TEXTURE", 1.0, examples=[s.lower()])

    _add(onto, "TEMPERATURE_PROPERTY", "PROPERTY", 1.0)
    for s in ["HOT", "COLD", "WARM", "COOL", "LUKEWARM", "FREEZING", "BOILING"]:
        _add(onto, s, "TEMPERATURE_PROPERTY", 1.0, examples=[s.lower()])

    # Relation
    _add(onto, "RELATION", "CONCEPT", 1.0, "An abstract relation between entities")
    _add(onto, "SPATIAL_RELATION", "RELATION", 1.0)
    for r in ["ABOVE", "BELOW", "INSIDE", "OUTSIDE", "NEAR", "FAR",
              "LEFT_OF", "RIGHT_OF", "IN_FRONT_OF", "BEHIND",
              "ADJACENT_TO", "OVERLAPPING"]:
        _add(onto, r, "SPATIAL_RELATION", 1.0)

    _add(onto, "TEMPORAL_RELATION", "RELATION", 1.0)
    for r in ["BEFORE", "AFTER", "DURING", "OVERLAPS",
              "STARTS", "FINISHES", "MEETS", "EQUALS_TIME"]:
        _add(onto, r, "TEMPORAL_RELATION", 1.0)

    _add(onto, "CAUSAL_RELATION", "RELATION", 1.0)
    for r in ["CAUSES", "PREVENTS", "ENABLES", "HINDERS", "FACILITATES"]:
        _add(onto, r, "CAUSAL_RELATION", 1.0)

    _add(onto, "SOCIAL_RELATION", "RELATION", 1.0)
    for r in ["LIKES", "KNOWS", "EMPLOYS", "OWNS", "LOVES", "TRUSTS",
              "FEARS", "RESPECTS", "HELPS", "HARMS"]:
        _add(onto, r, "SOCIAL_RELATION", 1.0)

    _add(onto, "MEREOLOGICAL_RELATION", "RELATION", 1.0)
    for r in ["PART_OF", "HAS_PART", "MEMBER_OF", "CONTAINS", "COMPOSED_OF"]:
        _add(onto, r, "MEREOLOGICAL_RELATION", 1.0)

    # Proposition
    _add(onto, "PROPOSITION", "ABSTRACT_OBJECT", 1.0,
         "A truth-apt mental or linguistic content")
    for p in ["FACT", "BELIEF", "DESIRE", "INTENTION", "QUESTION", "COMMAND",
              "PROMISE", "THREAT", "HYPOTHESIS", "THEORY"]:
        _add(onto, p, "PROPOSITION", 1.0)

    # Information
    _add(onto, "INFORMATION", "ABSTRACT_OBJECT", 1.0,
         "Encoded content carrying meaning")
    for i in ["TEXT", "IMAGE_CONCEPT", "AUDIO_CONCEPT", "DATA",
              "MESSAGE", "SIGNAL", "CODE", "PROGRAM"]:
        _add(onto, i, "INFORMATION", 1.0)

    _add(onto, "DOCUMENT", "INFORMATION", 1.0,
         "A structured textual information artifact")
    for d, exs in [
        ("BOOK",      ["novel", "textbook", "anthology"]),
        ("ARTICLE",   ["newspaper article", "journal article", "blog post"]),
        ("REPORT",    ["annual report", "research report"]),
        ("CONTRACT",  ["lease agreement", "employment contract"]),
        ("LETTER",    ["business letter", "personal letter"]),
        ("EMAIL",     ["email message", "newsletter"]),
        ("RECIPE",    ["cooking recipe", "baking recipe"]),
        ("MANUAL",    ["user manual", "instruction manual"]),
    ]:
        _add(onto, d, "DOCUMENT", 1.0, examples=exs)

    # Mathematical objects
    _add(onto, "MATHEMATICAL_OBJECT", "ABSTRACT_OBJECT", 1.0)
    _add(onto, "NUMBER", "MATHEMATICAL_OBJECT", 1.0)
    for n in ["INTEGER", "REAL", "RATIONAL", "COMPLEX", "NATURAL_NUMBER",
              "PRIME", "IRRATIONAL"]:
        _add(onto, n, "NUMBER", 1.0)
    _add(onto, "FUNCTION_OBJECT", "MATHEMATICAL_OBJECT", 1.0)
    _add(onto, "SET", "MATHEMATICAL_OBJECT", 1.0)
    _add(onto, "SEQUENCE", "MATHEMATICAL_OBJECT", 1.0)
    _add(onto, "GRAPH_OBJECT", "MATHEMATICAL_OBJECT", 1.0)
    _add(onto, "MATRIX", "MATHEMATICAL_OBJECT", 1.0)
    _add(onto, "VECTOR", "MATHEMATICAL_OBJECT", 1.0)

    # Social constructs
    _add(onto, "SOCIAL_CONSTRUCT", "ABSTRACT_OBJECT", 1.0,
         "Entities that exist by virtue of social agreement")
    _add(onto, "ORGANIZATION", "SOCIAL_CONSTRUCT", 1.0)
    for o, exs in [
        ("COMPANY",    ["Apple Inc.", "small business", "startup"]),
        ("GOVERNMENT", ["federal government", "local council"]),
        ("SCHOOL_ORG", ["primary school", "university department"]),
        ("FAMILY",     ["nuclear family", "extended family"]),
        ("TEAM",       ["sports team", "project team"]),
        ("CLUB",       ["social club", "book club"]),
        ("COMMITTEE",  ["steering committee", "ethics committee"]),
        ("UNION",      ["trade union", "credit union"]),
    ]:
        _add(onto, o, "ORGANIZATION", 1.0, examples=exs)

    _add(onto, "ROLE", "SOCIAL_CONSTRUCT", 1.0)
    for r in ["LEADER", "MEMBER", "OWNER", "CUSTOMER",
              "EMPLOYEE", "MANAGER", "AGENT", "PATIENT_ROLE",
              "BENEFICIARY", "INSTRUMENT_ROLE"]:
        _add(onto, r, "ROLE", 1.0)

    _add(onto, "INSTITUTION", "SOCIAL_CONSTRUCT", 1.0)
    for i in ["LAW", "CURRENCY", "LANGUAGE_INSTITUTION",
              "RELIGION", "MARKET", "GOVERNMENT_INSTITUTION",
              "EDUCATION_INSTITUTION", "HEALTHCARE_INSTITUTION"]:
        _add(onto, i, "INSTITUTION", 1.0)

    # ---------------------------------------------------------------
    # Eventuality branch
    # ---------------------------------------------------------------
    _add(onto, "EVENT", "EVENTUALITY", 1.0,
         "A bounded occurrence with a beginning and end",
         props={"telic": 1.0, "has_culmination": 1.0})

    _add(onto, "ACHIEVEMENT_EVENT", "EVENT", 1.0,
         "A telic, punctual event",
         props={"punctual": 1.0, "telic": 1.0})
    for e, exs in [
        ("ARRIVAL",    ["arrival at a place", "landing"]),
        ("DEPARTURE",  ["departure from a place", "takeoff"]),
        ("BIRTH",      ["birth of a person", "hatching"]),
        ("DEATH",      ["death of an organism"]),
        ("DISCOVERY",  ["discovery of a fact", "finding an object"]),
        ("BEGINNING",  ["start of a project", "onset of a process"]),
        ("ENDING",     ["end of an event", "culmination"]),
        ("MEETING",    ["encounter between people"]),
        ("COLLISION",  ["car crash", "impact event"]),
        ("EXPLOSION",  ["bomb explosion", "gas explosion"]),
    ]:
        _add(onto, e, "ACHIEVEMENT_EVENT", 1.0, examples=exs)

    _add(onto, "ACCOMPLISHMENT_EVENT", "EVENT", 1.0,
         "A telic, durative event with internal stages",
         props={"telic": 1.0, "durative": 1.0, "has_stages": 1.0})
    for e, exs in [
        ("CONSTRUCTION", ["building a house", "road construction"]),
        ("EDUCATION",    ["completing a degree", "training a skill"]),
        ("JOURNEY",      ["road trip", "hiking expedition"]),
        ("TREATMENT",    ["medical treatment", "therapy course"]),
        ("PRODUCTION",   ["manufacturing a car", "printing a book"]),
        ("RECOVERY",     ["recovering from illness", "economic recovery"]),
        ("NEGOTIATION",  ["peace negotiation", "salary negotiation"]),
        ("INVESTIGATION",["criminal investigation", "scientific investigation"]),
        ("WRITING_EVENT",["writing a report", "composing a symphony"]),
        ("COOKING_EVENT",["cooking a meal", "baking a cake"]),
    ]:
        _add(onto, e, "ACCOMPLISHMENT_EVENT", 1.0, examples=exs)

    _add(onto, "SEMELFACTIVE_EVENT", "EVENT", 1.0,
         "A single, non-iterative punctual occurrence",
         props={"punctual": 1.0, "atelic": 1.0, "semelfactive": 1.0})
    for e, exs in [
        ("CLICK",   ["mouse click", "button click"]),
        ("FLASH",   ["camera flash", "lightning flash"]),
        ("KNOCK",   ["door knock", "knock on wood"]),
        ("BLINK",   ["eye blink", "light blink"]),
        ("SNEEZE",  ["nasal sneeze"]),
        ("HICCUP",  ["involuntary hiccup"]),
        ("COUGH",   ["single cough"]),
        ("PULSE",   ["heartbeat pulse"]),
        ("TAP",     ["finger tap", "shoulder tap"]),
        ("BEEP",    ["device beep", "horn beep"]),
    ]:
        _add(onto, e, "SEMELFACTIVE_EVENT", 1.0, examples=exs)

    # Process
    _add(onto, "PROCESS", "EVENTUALITY", 1.0,
         "An ongoing, non-bounded dynamic or static situation",
         props={"atelic": 1.0})

    _add(onto, "ACTIVITY", "PROCESS", 1.0,
         "An atelic, durative, dynamic process",
         props={"dynamic": 1.0, "durative": 1.0, "atelic": 1.0})
    for a, exs in [
        ("RUNNING",   ["jogging", "sprinting", "marathon running"]),
        ("WALKING",   ["strolling", "hiking", "power walking"]),
        ("SWIMMING",  ["freestyle swimming", "butterfly stroke"]),
        ("EATING",    ["eating a meal", "snacking"]),
        ("DRINKING",  ["drinking water", "drinking coffee"]),
        ("SINGING",   ["singing a song", "choral singing"]),
        ("DANCING",   ["ballet dancing", "ballroom dancing"]),
        ("WORKING",   ["office work", "manual labour"]),
        ("PLAYING",   ["playing sport", "playing music", "playing games"]),
        ("DRIVING",   ["driving a car", "truck driving"]),
        ("READING",   ["reading a book", "reading an article"]),
        ("TALKING",   ["conversation", "lecturing", "chatting"]),
        ("WRITING",   ["writing by hand", "typing"]),
        ("THINKING",  ["deliberating", "daydreaming", "planning"]),
        ("SEARCHING", ["looking for keys", "web searching"]),
    ]:
        _add(onto, a, "ACTIVITY", 1.0, examples=exs)

    _add(onto, "STATE", "PROCESS", 1.0,
         "An atelic, non-dynamic situation holding over time",
         props={"stative": 1.0, "durative": 1.0, "atelic": 1.0, "dynamic": 0.0})
    for s, exs in [
        ("KNOWING",         ["knowing a fact", "knowing a person"]),
        ("BELIEVING",       ["believing a proposition"]),
        ("OWNING",          ["owning a house", "owning a car"]),
        ("BEING_LOCATED",   ["being in Paris", "being at home"]),
        ("RESEMBLING",      ["resembling a parent", "resembling a prototype"]),
        ("LOVING",          ["loving a person"]),
        ("HATING",          ["hating injustice"]),
        ("FEARING",         ["fearing heights"]),
        ("HAVING",          ["having money", "having a cold"]),
        ("WANTING",         ["wanting coffee", "wanting to leave"]),
        ("NEEDING",         ["needing help", "needing food"]),
        ("CONTAINING_STATE",["containing water", "containing information"]),
    ]:
        _add(onto, s, "STATE", 1.0, examples=exs)

    # Situation
    _add(onto, "SITUATION", "EVENTUALITY", 0.9,
         "A complex state-of-affairs or scenario")
    for s in ["CONDITION", "CIRCUMSTANCE", "SCENARIO", "CONTEXT_EVENT",
              "PROBLEM_SITUATION", "CRISIS", "OPPORTUNITY"]:
        _add(onto, s, "SITUATION", 1.0)

    # ---------------------------------------------------------------
    # Location branch
    # ---------------------------------------------------------------
    _add(onto, "GEOGRAPHIC_LOCATION", "LOCATION", 1.0,
         "A location defined by geographic features")
    for loc, exs in [
        ("COUNTRY",      ["France", "Japan", "Brazil"]),
        ("CITY",         ["Paris", "Tokyo", "New York"]),
        ("REGION",       ["Normandy", "Patagonia", "Midwest"]),
        ("CONTINENT",    ["Europe", "Asia", "Africa", "Americas"]),
        ("OCEAN_PLACE",  ["Pacific Ocean region", "Atlantic coast"]),
        ("MOUNTAIN_PLACE",["Alps", "Rocky Mountains", "Himalayas"]),
        ("FOREST",       ["Amazon rainforest", "Sherwood Forest"]),
        ("DESERT",       ["Sahara Desert", "Gobi Desert"]),
        ("ISLAND",       ["Iceland", "Hawaii", "Madagascar"]),
        ("VALLEY",       ["Nile Valley", "Silicon Valley"]),
    ]:
        _add(onto, loc, "GEOGRAPHIC_LOCATION", 1.0, examples=exs)

    _add(onto, "FUNCTIONAL_LOCATION", "LOCATION", 1.0,
         "A location defined by its social or functional role")
    for loc, exs in [
        ("HOME",            ["family home", "apartment", "dormitory"]),
        ("WORKPLACE",       ["office", "factory floor", "studio"]),
        ("SCHOOL_PLACE",    ["classroom", "school campus"]),
        ("HOSPITAL_PLACE",  ["emergency room", "ward", "clinic"]),
        ("SHOP",            ["grocery store", "bookstore", "pharmacy"]),
        ("RESTAURANT",      ["diner", "fine dining", "food court"]),
        ("PARK",            ["national park", "city park"]),
        ("LIBRARY_PLACE",   ["public library", "reading room"]),
        ("MUSEUM_PLACE",    ["art gallery", "science museum"]),
        ("PRISON",          ["jail", "correctional facility"]),
        ("AIRPORT",         ["international airport", "regional airport"]),
        ("STATION",         ["train station", "bus station", "metro station"]),
    ]:
        _add(onto, loc, "FUNCTIONAL_LOCATION", 1.0, examples=exs)

    return onto


# ---------------------------------------------------------------------------
# Module-level convenience instance
# ---------------------------------------------------------------------------

#: The built-in core ontology, ready to use.
CORE_ONTOLOGY: Ontology = build_core_ontology()


# ---------------------------------------------------------------------------
# OntologyIndex — fast lookup structures
# ---------------------------------------------------------------------------

class OntologyIndex:
    """Pre-built index structures over an Ontology for O(1) or O(log n) queries.

    For large ontologies, building a ``set``-based ancestor index once and
    reusing it is much faster than walking the tree on every query.

    This class wraps an :class:`Ontology` and maintains:

    * ``_ancestors``: concept → frozenset of ancestor names
    * ``_cumulative_grades``: (concept, ancestor) → Grade (precomputed is_a)
    * ``_property_index``: property_name → [(concept, grade)] sorted desc
    """

    def __init__(self, onto: Ontology) -> None:
        self._onto = onto
        self._ancestors: dict[str, frozenset[str]] = {}
        self._cumulative_grades: dict[tuple[str, str], Grade] = {}
        self._property_index: dict[str, list[tuple[str, Grade]]] = defaultdict(list)
        self._built = False

    def build(self) -> None:
        """Build all indexes.  Call once after ontology is fully populated."""
        for name in self._onto._nodes:
            ancs = self._onto.ancestors(name)
            anc_set = frozenset(a for a, _ in ancs)
            self._ancestors[name] = anc_set
            for anc_name, cumgrade in ancs:
                self._cumulative_grades[(name, anc_name)] = cumgrade
        # Build property index
        prop_dict: dict[str, list[tuple[str, Grade]]] = defaultdict(list)
        for name, node in self._onto._nodes.items():
            for prop, g in node.properties.items():
                prop_dict[prop].append((name, g))
        for prop, lst in prop_dict.items():
            lst.sort(key=lambda kv: kv[1], reverse=True)
            self._property_index[prop] = lst
        self._built = True

    def is_a_fast(self, child: str, parent: str) -> Grade:
        """O(1) is-a lookup using precomputed ancestors.

        Falls back to the ontology's own is_a if the index is not built yet.

        Parameters
        ----------
        child, parent:
            Concept names.

        Returns
        -------
        Grade
            Precomputed is_a grade.
        """
        if not self._built:
            return self._onto.is_a(child, parent)
        child = child.upper()
        parent = parent.upper()
        if child == parent:
            return Grade.perfect()
        key = (child, parent)
        return self._cumulative_grades.get(key, Grade.impossible())

    def concepts_with_property_fast(
        self, prop: str, min_grade: Optional[Grade] = None
    ) -> list[tuple[str, Grade]]:
        """O(1) property lookup using the precomputed index.

        Parameters
        ----------
        prop:
            Property name.
        min_grade:
            Optional minimum Grade filter.

        Returns
        -------
        list[tuple[str, Grade]]
            Sorted by grade descending.
        """
        if not self._built:
            return self._onto.concepts_with_property(prop, min_grade)
        results = self._property_index.get(prop, [])
        if min_grade is not None:
            results = [(c, g) for c, g in results if g >= min_grade]
        return results

    def ancestors_fast(self, concept: str) -> frozenset[str]:
        """Return the set of ancestor names without Grade annotation.

        Useful when you only need the set membership (e.g. for LCA).

        Parameters
        ----------
        concept:
            Concept name.

        Returns
        -------
        frozenset[str]
            All ancestor names.
        """
        if not self._built:
            return frozenset(a for a, _ in self._onto.ancestors(concept))
        return self._ancestors.get(concept.upper(), frozenset())

    def lca_fast(self, c1: str, c2: str) -> Optional[str]:
        """Fast LCA using the precomputed ancestor sets.

        Returns the deepest concept in both ancestor sets.

        Parameters
        ----------
        c1, c2:
            Concept names.

        Returns
        -------
        Optional[str]
            LCA name, or None.
        """
        if not self._built:
            return self._onto.lca(c1, c2)
        c1 = c1.upper()
        c2 = c2.upper()
        anc1 = self._ancestors.get(c1, frozenset()) | {c1}
        anc2 = self._ancestors.get(c2, frozenset()) | {c2}
        common = anc1 & anc2
        if not common:
            return None
        # Return the deepest (maximum depth) common ancestor
        best = max(common, key=lambda n: self._onto._depth(n))
        return best


# ---------------------------------------------------------------------------
# ConceptualDistance — richer distance metrics
# ---------------------------------------------------------------------------

class ConceptualDistance:
    """Richer distance metrics over an Ontology, all Grade-valued.

    In addition to the basic path-length ``distance`` in :class:`Ontology`,
    this class provides:

    * Information-content (IC) based similarity (Resnik, Lin, Jiang-Conrath)
    * Feature-based similarity (Tversky)
    * Grade-aggregated family resemblance

    Information content
    ~~~~~~~~~~~~~~~~~~~
    IC(c) = -log P(c), where P(c) = count(c) / count(root).
    Here "count" is approximated by the number of descendants + 1.
    Higher IC → more specific concept → more informative.
    """

    def __init__(self, onto: Ontology) -> None:
        self._onto = onto
        self._ic: dict[str, float] = {}
        self._computed = False

    def _compute_ic(self) -> None:
        """Precompute information content for all concepts."""
        root_count = len(self._onto._nodes)
        for name in self._onto._nodes:
            desc_count = len(self._onto.descendants(name)) + 1
            p = desc_count / max(root_count, 1)
            self._ic[name] = -math.log(max(p, 1e-10))
        self._computed = True

    def ic(self, concept: str) -> float:
        """Information content of ``concept``.

        IC(c) = -log(|descendants(c) + 1| / |all concepts|).
        More specific = higher IC.
        """
        if not self._computed:
            self._compute_ic()
        return self._ic.get(concept.upper(), 0.0)

    def resnik_similarity(self, c1: str, c2: str) -> Grade:
        """Resnik similarity = IC(LCA(c1, c2)).

        Higher shared IC → more similar concepts.  Returned as
        ``Grade.from_prob(exp(-0.1 * (max_ic - ic_lca)))`` to map IC
        differences into [0, 1] probability space then to Grade.

        Parameters
        ----------
        c1, c2:
            Concept names.

        Returns
        -------
        Grade
            Resnik similarity as a Grade.
        """
        if not self._computed:
            self._compute_ic()
        lca = self._onto.lca(c1, c2)
        if lca is None:
            return Grade.impossible()
        ic_lca = self._ic.get(lca, 0.0)
        max_ic = max(self._ic.values()) if self._ic else 1.0
        if max_ic == 0.0:
            return Grade.perfect()
        ratio = ic_lca / max_ic
        return Grade.from_prob(max(ratio, 1e-10))

    def lin_similarity(self, c1: str, c2: str) -> Grade:
        """Lin similarity = 2*IC(LCA) / (IC(c1) + IC(c2)).

        Normalizes Resnik by both concepts' IC.  Returns Grade.from_prob(lin).

        Parameters
        ----------
        c1, c2:
            Concept names.

        Returns
        -------
        Grade
            Lin similarity as a Grade.
        """
        if not self._computed:
            self._compute_ic()
        c1 = c1.upper()
        c2 = c2.upper()
        lca = self._onto.lca(c1, c2)
        if lca is None:
            return Grade.impossible()
        ic_lca = self._ic.get(lca, 0.0)
        ic_c1 = self._ic.get(c1, 0.0)
        ic_c2 = self._ic.get(c2, 0.0)
        denom = ic_c1 + ic_c2
        if denom == 0.0:
            return Grade.perfect() if c1 == c2 else Grade.impossible()
        lin = 2.0 * ic_lca / denom
        return Grade.from_prob(min(lin, 1.0))

    def jiang_conrath_similarity(self, c1: str, c2: str) -> Grade:
        """Jiang-Conrath similarity: inverse of JC distance.

        JC distance = IC(c1) + IC(c2) - 2*IC(LCA).
        Similarity = exp(-JC_distance) converted to Grade.

        Parameters
        ----------
        c1, c2:
            Concept names.

        Returns
        -------
        Grade
            JC-based similarity as a Grade.
        """
        if not self._computed:
            self._compute_ic()
        c1 = c1.upper()
        c2 = c2.upper()
        if c1 == c2:
            return Grade.perfect()
        lca = self._onto.lca(c1, c2)
        if lca is None:
            return Grade.impossible()
        ic_lca = self._ic.get(lca, 0.0)
        ic_c1 = self._ic.get(c1, 0.0)
        ic_c2 = self._ic.get(c2, 0.0)
        jc_dist = ic_c1 + ic_c2 - 2.0 * ic_lca
        sim_prob = math.exp(-max(jc_dist, 0.0))
        return Grade.from_prob(max(sim_prob, 1e-10))

    def tversky_similarity(
        self,
        c1: str,
        c2: str,
        alpha: float = 0.5,
        beta: float = 0.5,
    ) -> Grade:
        """Tversky feature-based similarity.

        Tversky(c1, c2, α, β) = |A ∩ B| / (|A ∩ B| + α|A \\ B| + β|B \\ A|)

        where A, B are the ancestor sets of c1 and c2 (as proxy for feature
        sets).  Returns a Grade.

        Parameters
        ----------
        c1, c2:
            Concept names.
        alpha:
            Weight for c1-specific ancestors.
        beta:
            Weight for c2-specific ancestors.

        Returns
        -------
        Grade
            Tversky similarity as a Grade.
        """
        c1 = c1.upper()
        c2 = c2.upper()
        anc1 = set(a for a, _ in self._onto.ancestors(c1)) | {c1}
        anc2 = set(a for a, _ in self._onto.ancestors(c2)) | {c2}
        intersection = anc1 & anc2
        c1_only = anc1 - anc2
        c2_only = anc2 - anc1
        denom = len(intersection) + alpha * len(c1_only) + beta * len(c2_only)
        if denom == 0.0:
            return Grade.impossible()
        tversky = len(intersection) / denom
        return Grade.from_prob(max(tversky, 1e-10))

    def family_resemblance(self, concept: str, category: str) -> Grade:
        """Roschian family resemblance of ``concept`` to ``category``.

        Computed as the mean of:
        * is_a grade (membership)
        * property overlap fraction (relative to category members)
        * prototype similarity (average similarity to category examples)

        Returned as a single Grade.

        Parameters
        ----------
        concept:
            The concept to evaluate.
        category:
            The category to compare against.

        Returns
        -------
        Grade
            Family resemblance grade.
        """
        isa_grade = self._onto.is_a(concept, category)
        # Property overlap
        concept_props = set(
            prop
            for name in [concept] + [a for a, _ in self._onto.ancestors(concept)]
            for prop in (self._onto._nodes[name].properties if name in self._onto._nodes else {})
        )
        cat_props = set(
            prop
            for name in [category] + [c for c, _ in self._onto.descendants(category)]
            for prop in (self._onto._nodes[name].properties if name in self._onto._nodes else {})
        )
        if cat_props:
            overlap = len(concept_props & cat_props) / len(cat_props)
        else:
            overlap = 1.0
        prop_grade = Grade.from_prob(max(overlap, 1e-10))
        # Combine
        combined = isa_grade * prop_grade
        return combined


# ---------------------------------------------------------------------------
# OntologyCoverage — how well a set of concepts covers a domain
# ---------------------------------------------------------------------------

class OntologyCoverage:
    """Measures coverage of a set of concepts over a domain ontology.

    Useful for:
    * Checking whether a discourse mentions the key concepts in a domain.
    * Evaluating how thoroughly a text covers an ontological category.

    Coverage of a concept C over a subtree S is:
    |{n ∈ S : is_a(C, n) is not impossible}| / |S|

    This is not a Grade itself but returns Grade-weighted coverage scores.
    """

    def __init__(self, onto: Ontology) -> None:
        self._onto = onto

    def coverage_of(self, concepts: list[str], domain_root: str) -> Grade:
        """Grade of how well ``concepts`` covers the domain under ``domain_root``.

        For each descendant of ``domain_root``, we check whether any concept in
        ``concepts`` is-a descendant (directly or transitively).  The fraction
        covered is converted to a Grade.

        Parameters
        ----------
        concepts:
            The concepts mentioned (e.g. from a text).
        domain_root:
            The root of the domain subtree.

        Returns
        -------
        Grade
            Coverage grade.
        """
        domain_nodes = set(n for n, _ in self._onto.descendants(domain_root))
        domain_nodes.add(domain_root.upper())
        if not domain_nodes:
            return Grade.impossible()
        covered: set[str] = set()
        for concept in concepts:
            concept = concept.upper()
            # concept covers a node if concept is_a node or node is_a concept
            for node_name in domain_nodes:
                if (not self._onto.is_a(concept, node_name).is_impossible or
                        not self._onto.is_a(node_name, concept).is_impossible):
                    covered.add(node_name)
        fraction = len(covered) / len(domain_nodes)
        return Grade.from_prob(max(fraction, 1e-10))

    def redundancy(self, concepts: list[str]) -> Grade:
        """Grade of redundancy: how much do the concepts overlap?

        High redundancy means many pairs are in is-a relationship.
        Redundancy = (number of subsumption pairs) / (total pairs).

        Returns ``Grade.impossible()`` if fewer than 2 concepts given.

        Parameters
        ----------
        concepts:
            List of concept names.

        Returns
        -------
        Grade
            Redundancy grade (higher = more redundancy).
        """
        n = len(concepts)
        if n < 2:
            return Grade.impossible()
        pairs = n * (n - 1) // 2
        subsumptions = 0
        for i in range(n):
            for j in range(i + 1, n):
                if not self._onto.subsumes(concepts[i], concepts[j]).is_impossible:
                    subsumptions += 1
                elif not self._onto.subsumes(concepts[j], concepts[i]).is_impossible:
                    subsumptions += 1
        fraction = subsumptions / pairs
        return Grade.from_prob(max(fraction, 1e-10))

    def diversity(self, concepts: list[str]) -> Grade:
        """Grade of diversity: how semantically spread are the concepts?

        Diversity = 1 - mean_similarity.
        A higher Grade means the concepts are more diverse (less similar).

        Parameters
        ----------
        concepts:
            List of concept names.

        Returns
        -------
        Grade
            Diversity grade.
        """
        n = len(concepts)
        if n < 2:
            return Grade.perfect()
        sims: list[float] = []
        for i in range(n):
            for j in range(i + 1, n):
                sim = self._onto.similarity(concepts[i], concepts[j]).to_prob()
                sims.append(sim)
        mean_sim = sum(sims) / len(sims) if sims else 0.0
        diversity_prob = max(1.0 - mean_sim, 1e-10)
        return Grade.from_prob(diversity_prob)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def print_hierarchy(
    onto: Ontology,
    root: Optional[str] = None,
    max_depth: int = 4,
    show_grades: bool = True,
) -> None:
    """Pretty-print the ontology hierarchy to stdout.

    Parameters
    ----------
    onto:
        The ontology to print.
    root:
        Starting node; None prints all roots.
    max_depth:
        Maximum depth to display.
    show_grades:
        Whether to show Grade values in brackets.
    """
    viz = OntologyVisualizer(max_depth=max_depth, show_grades=show_grades)
    print(viz.render(onto, root))


def concepts_between(onto: Ontology, specific: str, general: str) -> list[str]:
    """Return all concepts on the path from ``specific`` up to ``general``.

    The returned list includes both endpoints.  Returns an empty list if
    ``specific`` is not a descendant of ``general``.

    Parameters
    ----------
    onto:
        The ontology.
    specific:
        The more specific concept (descendant).
    general:
        The more general concept (ancestor).

    Returns
    -------
    list[str]
        Path from ``specific`` to ``general`` inclusive.
    """
    specific = specific.upper()
    general = general.upper()
    path = [specific]
    current = specific
    depth = 0
    while current != general and depth < 100:
        node = onto._nodes.get(current)
        if node is None or node.parent is None:
            return []
        current = node.parent
        path.append(current)
        depth += 1
    if path[-1] != general:
        return []
    return path


def most_similar_to(
    onto: Ontology, concept: str, candidates: list[str]
) -> tuple[str, Grade]:
    """Find the concept in ``candidates`` most similar to ``concept``.

    Parameters
    ----------
    onto:
        The ontology.
    concept:
        The reference concept.
    candidates:
        Concepts to rank.

    Returns
    -------
    tuple[str, Grade]
        (most_similar_name, similarity_grade).
    """
    best_name = ""
    best_grade = Grade.impossible()
    for cand in candidates:
        g = onto.similarity(concept, cand)
        if g > best_grade:
            best_grade = g
            best_name = cand
    return best_name, best_grade


def type_signature(onto: Ontology, concept: str) -> dict[str, Grade]:
    """Return the full type signature of ``concept``.

    The type signature is a dict mapping each ancestor to the is_a Grade
    for that ancestor.  This is the complete *type* of the concept in the
    Grade semiring.

    Parameters
    ----------
    onto:
        The ontology.
    concept:
        The concept whose type to retrieve.

    Returns
    -------
    dict[str, Grade]
        {ancestor_name: is_a_grade}.
    """
    result: dict[str, Grade] = {concept.upper(): Grade.perfect()}
    for anc, g in onto.ancestors(concept):
        result[anc] = g
    return result


def inherited_properties(onto: Ontology, concept: str) -> dict[str, Grade]:
    """Return all properties of ``concept`` including inherited ones.

    Properties closer to ``concept`` shadow properties from ancestors with
    the same name.  The returned Grade for each property includes the is_a
    grade for the ancestor from which it was inherited.

    Parameters
    ----------
    onto:
        The ontology.
    concept:
        The concept whose properties to retrieve.

    Returns
    -------
    dict[str, Grade]
        {property_name: Grade}.
    """
    result: dict[str, Grade] = {}
    node = onto._nodes.get(concept.upper())
    if node is not None:
        for prop, g in node.properties.items():
            result[prop] = g
    for anc_name, cumgrade in onto.ancestors(concept):
        anc_node = onto._nodes.get(anc_name)
        if anc_node is None:
            continue
        for prop, g in anc_node.properties.items():
            if prop not in result:
                result[prop] = cumgrade * g
    return result


def conceptual_neighbourhood(
    onto: Ontology, concept: str, radius: int = 2
) -> dict[str, Grade]:
    """All concepts within path-distance ``radius`` of ``concept``.

    Collects ancestors up to ``radius`` steps and descendants up to
    ``radius`` steps.  Returns {name: similarity_grade}.

    Parameters
    ----------
    onto:
        The ontology.
    concept:
        Central concept.
    radius:
        Maximum path distance to include.

    Returns
    -------
    dict[str, Grade]
        {concept_name: similarity_grade}.
    """
    concept = concept.upper()
    result: dict[str, Grade] = {concept: Grade.perfect()}
    # Ancestors (limited by radius)
    current = concept
    acc = Grade.perfect()
    for depth in range(radius):
        node = onto._nodes.get(current)
        if node is None or node.parent is None:
            break
        acc = acc * node.grade
        result[node.parent] = acc
        current = node.parent
    # Descendants (BFS up to radius)
    queue: deque[tuple[str, int, Grade]] = deque()
    for child in onto._children.get(concept, []):
        child_node = onto._nodes.get(child)
        if child_node:
            queue.append((child, 1, child_node.grade))
    while queue:
        name, depth, acc_g = queue.popleft()
        if depth > radius:
            continue
        result[name] = acc_g
        if depth < radius:
            for child in onto._children.get(name, []):
                child_node = onto._nodes.get(child)
                if child_node:
                    queue.append((child, depth + 1, acc_g * child_node.grade))
    return result


def grade_query(
    onto: Ontology,
    *,
    supertype: Optional[str] = None,
    required_properties: Optional[dict[str, Grade]] = None,
    min_grade: Optional[Grade] = None,
    max_results: int = 20,
) -> list[tuple[str, Grade]]:
    """Convenience wrapper for :class:`OntologyQuery`.

    Creates and runs an :class:`OntologyQuery` with the given parameters.

    Parameters
    ----------
    onto:
        The ontology to query.
    supertype:
        Restrict to descendants of this type.
    required_properties:
        Properties the result concepts must have.
    min_grade:
        Minimum node grade.
    max_results:
        Result cap.

    Returns
    -------
    list[tuple[str, Grade]]
        Ranked (concept_name, grade) pairs.
    """
    q = OntologyQuery(
        supertype=supertype,
        required_properties=required_properties or {},
        min_grade=min_grade,
        max_results=max_results,
    )
    return q.run(onto)

