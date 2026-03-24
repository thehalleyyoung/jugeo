"""DOM site — the topological space of CSS selectors over the DOM.

The DOM is modelled as a presheaf on the category of CSS selectors.
``DOMSite`` is the underlying site: nodes, parent-child edges, and
selector-based open-cover structure.
"""

from __future__ import annotations

import html.parser
import re
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from .models import (
    CSSValue,
    ComputedStyle,
    DOMNode,
    DOMNodeKind,
)


# ---------------------------------------------------------------------------
# Selector combinators
# ---------------------------------------------------------------------------

class CombinatorKind(str, Enum):
    """CSS combinator between selector parts."""
    DESCENDANT = " "
    CHILD = ">"
    ADJACENT_SIBLING = "+"
    GENERAL_SIBLING = "~"


# ---------------------------------------------------------------------------
# Selector AST
# ---------------------------------------------------------------------------

@dataclass
class SelectorPart:
    """One simple-selector segment (tag, id, classes, attrs, pseudo-classes)."""

    tag: str = ""
    id_attr: str = ""
    classes: list[str] = field(default_factory=list)
    pseudo_classes: list[str] = field(default_factory=list)
    attributes: list[tuple[str, str, str]] = field(default_factory=list)

    def matches_node(self, node: DOMNode) -> bool:
        """Return True if *node* satisfies every constraint in this part."""
        if node.node_kind != DOMNodeKind.ELEMENT:
            return False
        if self.tag and self.tag != "*" and self.tag != node.tag:
            return False
        if self.id_attr and self.id_attr != node.id_attr:
            return False
        for cls in self.classes:
            if cls not in node.classes:
                return False
        for attr_name, op, value in self.attributes:
            node_val = node.attributes.get(attr_name)
            if node_val is None:
                # Also check id_attr and classes as special attrs
                if attr_name == "id":
                    node_val = node.id_attr
                elif attr_name == "class":
                    node_val = " ".join(node.classes)
                else:
                    return False
            if op == "" or op == "exists":
                continue  # just check existence
            if op == "=":
                if node_val != value:
                    return False
            elif op == "^=":
                if not node_val.startswith(value):
                    return False
            elif op == "$=":
                if not node_val.endswith(value):
                    return False
            elif op == "*=":
                if value not in node_val:
                    return False
            elif op == "~=":
                if value not in node_val.split():
                    return False
            elif op == "|=":
                if node_val != value and not node_val.startswith(value + "-"):
                    return False
        return True


@dataclass
class SelectorChain:
    """A compound selector chain: parts connected by combinators."""

    parts: list[SelectorPart] = field(default_factory=list)
    combinators: list[CombinatorKind] = field(default_factory=list)

    @property
    def is_simple(self) -> bool:
        return len(self.parts) <= 1 and not self.combinators


# ---------------------------------------------------------------------------
# Selector parser
# ---------------------------------------------------------------------------

class SelectorParser:
    """Parse a CSS selector string into a ``SelectorChain``."""

    # Regex to tokenise a simple selector
    _TOKEN_RE = re.compile(
        r"(?P<id>#[\w-]+)"
        r"|(?P<cls>\.[\w-]+)"
        r"|(?P<attr>\[[^\]]*\])"
        r"|(?P<pseudo_elem>::[\w-]+)"
        r"|(?P<pseudo_cls>:[\w-]+(?:\([^)]*\))?)"
        r"|(?P<tag>[\w*][\w-]*)"
    )

    _COMBINATOR_RE = re.compile(r"\s*([>+~])\s*")

    # ------------------------------------------------------------------ API

    @classmethod
    def parse_selector(cls, s: str) -> SelectorChain:
        """Parse a CSS selector string.

        Comma-separated lists: only the first alternative is returned
        (matching will union results externally).
        """
        # Handle comma-separated list — just take first
        top = cls._split_comma(s)
        sel = top[0].strip()
        return cls._parse_single(sel)

    @classmethod
    def matches(cls, chain: SelectorChain, node: DOMNode, dom: DOMSite) -> bool:
        """Check whether *node* matches the full *chain* inside *dom*."""
        if not chain.parts:
            return False
        # Work backwards from the rightmost part
        if not chain.parts[-1].matches_node(node):
            return False
        if len(chain.parts) == 1:
            return True
        return cls._match_backwards(chain, len(chain.parts) - 2, node, dom)

    # ------------------------------------------------------------ internal

    @classmethod
    def _split_comma(cls, s: str) -> list[str]:
        """Split on commas not inside parentheses."""
        parts: list[str] = []
        depth = 0
        cur: list[str] = []
        for ch in s:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        parts.append("".join(cur))
        return parts

    @classmethod
    def _parse_single(cls, s: str) -> SelectorChain:
        """Parse one (non-comma) selector into a chain."""
        # Strip pseudo-elements for matching
        s = re.sub(r"::[\w-]+", "", s)
        # Strip pseudo-function content for matching (keep inner for specificity elsewhere)
        s = re.sub(r":(?:not|is|where|has)\([^)]*\)", "", s)
        s = s.strip()
        if not s:
            return SelectorChain()

        # Split on combinators while preserving them
        tokens: list[str] = []
        combs: list[CombinatorKind] = []

        # Tokenise: split by combinator chars (outside brackets)
        i = 0
        buf: list[str] = []
        while i < len(s):
            ch = s[i]
            if ch == "[":
                j = s.index("]", i) + 1
                buf.append(s[i:j])
                i = j
                continue
            if ch in ">+~":
                part_str = "".join(buf).strip()
                if part_str:
                    tokens.append(part_str)
                if ch == ">":
                    combs.append(CombinatorKind.CHILD)
                elif ch == "+":
                    combs.append(CombinatorKind.ADJACENT_SIBLING)
                else:
                    combs.append(CombinatorKind.GENERAL_SIBLING)
                buf = []
                i += 1
                continue
            # whitespace may be descendant combinator
            if ch == " ":
                # skip extra spaces
                while i < len(s) and s[i] == " ":
                    i += 1
                # peek: if next char is a combinator, it wins
                if i < len(s) and s[i] in ">+~":
                    continue
                part_str = "".join(buf).strip()
                if part_str:
                    tokens.append(part_str)
                    combs.append(CombinatorKind.DESCENDANT)
                    buf = []
                continue
            buf.append(ch)
            i += 1
        tail = "".join(buf).strip()
        if tail:
            tokens.append(tail)

        parts = [cls._parse_part(t) for t in tokens]
        return SelectorChain(parts=parts, combinators=combs)

    @classmethod
    def _parse_part(cls, s: str) -> SelectorPart:
        """Parse a simple selector like ``div.foo#bar[href]``."""
        part = SelectorPart()
        for m in cls._TOKEN_RE.finditer(s):
            if m.group("tag"):
                part.tag = m.group("tag")
            elif m.group("id"):
                part.id_attr = m.group("id")[1:]  # strip #
            elif m.group("cls"):
                part.classes.append(m.group("cls")[1:])  # strip .
            elif m.group("attr"):
                attr_str = m.group("attr")[1:-1]  # strip []
                part.attributes.append(cls._parse_attr(attr_str))
            elif m.group("pseudo_cls"):
                part.pseudo_classes.append(m.group("pseudo_cls"))
        return part

    @staticmethod
    def _parse_attr(attr_str: str) -> tuple[str, str, str]:
        """Parse attribute selector content like ``href``, ``href=foo``."""
        for op in ("^=", "$=", "*=", "~=", "|=", "="):
            if op in attr_str:
                name, val = attr_str.split(op, 1)
                val = val.strip().strip("'\"")
                return (name.strip(), op, val)
        return (attr_str.strip(), "exists", "")

    @classmethod
    def _match_backwards(
        cls,
        chain: SelectorChain,
        idx: int,
        current_node: DOMNode,
        dom: DOMSite,
    ) -> bool:
        """Recursively check combinators from right to left."""
        comb = chain.combinators[idx]
        target_part = chain.parts[idx]

        if comb == CombinatorKind.DESCENDANT:
            # Any ancestor must match
            nid = current_node.parent_id
            while nid and nid in dom.nodes:
                ancestor = dom.nodes[nid]
                if target_part.matches_node(ancestor):
                    if idx == 0:
                        return True
                    if cls._match_backwards(chain, idx - 1, ancestor, dom):
                        return True
                nid = ancestor.parent_id
            return False

        if comb == CombinatorKind.CHILD:
            if not current_node.parent_id or current_node.parent_id not in dom.nodes:
                return False
            parent = dom.nodes[current_node.parent_id]
            if not target_part.matches_node(parent):
                return False
            if idx == 0:
                return True
            return cls._match_backwards(chain, idx - 1, parent, dom)

        if comb == CombinatorKind.ADJACENT_SIBLING:
            prev = cls._prev_sibling(current_node, dom)
            if prev is None or not target_part.matches_node(prev):
                return False
            if idx == 0:
                return True
            return cls._match_backwards(chain, idx - 1, prev, dom)

        if comb == CombinatorKind.GENERAL_SIBLING:
            siblings = cls._preceding_siblings(current_node, dom)
            for sib in siblings:
                if target_part.matches_node(sib):
                    if idx == 0:
                        return True
                    if cls._match_backwards(chain, idx - 1, sib, dom):
                        return True
            return False

        return False

    @staticmethod
    def _prev_sibling(node: DOMNode, dom: DOMSite) -> DOMNode | None:
        if not node.parent_id or node.parent_id not in dom.nodes:
            return None
        parent = dom.nodes[node.parent_id]
        children = parent.children
        idx = None
        for i, cid in enumerate(children):
            if cid == node.node_id:
                idx = i
                break
        if idx is None or idx == 0:
            return None
        # Find the preceding element sibling
        for i in range(idx - 1, -1, -1):
            sib = dom.nodes.get(children[i])
            if sib and sib.node_kind == DOMNodeKind.ELEMENT:
                return sib
        return None

    @staticmethod
    def _preceding_siblings(node: DOMNode, dom: DOMSite) -> list[DOMNode]:
        if not node.parent_id or node.parent_id not in dom.nodes:
            return []
        parent = dom.nodes[node.parent_id]
        children = parent.children
        result: list[DOMNode] = []
        for cid in children:
            if cid == node.node_id:
                break
            sib = dom.nodes.get(cid)
            if sib and sib.node_kind == DOMNodeKind.ELEMENT:
                result.append(sib)
        return result


# ---------------------------------------------------------------------------
# Internal HTML parser
# ---------------------------------------------------------------------------

_VOID_ELEMENTS = frozenset({
    "br", "img", "input", "hr", "meta", "link", "area", "base",
    "col", "embed", "param", "source", "track", "wbr",
})


class _HTMLParser(html.parser.HTMLParser):
    """Build a ``DOMSite`` from HTML source."""

    def __init__(self) -> None:
        super().__init__()
        self._counter = 0
        self.nodes: dict[str, DOMNode] = {}
        self._stack: list[str] = []  # stack of open node_ids
        self.root_id: str = ""

    def _next_id(self) -> str:
        self._counter += 1
        return f"n{self._counter}"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        nid = self._next_id()
        attr_dict: dict[str, str] = {}
        id_attr = ""
        classes: list[str] = []
        for name, val in attrs:
            v = val if val is not None else ""
            attr_dict[name] = v
            if name == "id":
                id_attr = v
            elif name == "class":
                classes = [c for c in v.split() if c]

        node = DOMNode(
            node_id=nid,
            tag=tag,
            node_kind=DOMNodeKind.ELEMENT,
            id_attr=id_attr,
            classes=classes,
            attributes=attr_dict,
        )

        # Link to parent
        if self._stack:
            parent_id = self._stack[-1]
            node.parent_id = parent_id
            self.nodes[parent_id].children.append(nid)
        else:
            self.root_id = nid

        self.nodes[nid] = node

        if tag.lower() not in _VOID_ELEMENTS:
            self._stack.append(nid)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _VOID_ELEMENTS:
            return
        # Pop from stack — be lenient with mis-nesting
        for i in range(len(self._stack) - 1, -1, -1):
            if self.nodes[self._stack[i]].tag == tag:
                self._stack.pop(i)
                return
        # If not found, just pop the top if present (lenient)
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        nid = self._next_id()
        node = DOMNode(
            node_id=nid,
            node_kind=DOMNodeKind.TEXT,
            text_content=data,
        )
        if self._stack:
            parent_id = self._stack[-1]
            node.parent_id = parent_id
            self.nodes[parent_id].children.append(nid)
        else:
            self.root_id = self.root_id or nid
        self.nodes[nid] = node

    def handle_comment(self, data: str) -> None:
        nid = self._next_id()
        node = DOMNode(
            node_id=nid,
            node_kind=DOMNodeKind.COMMENT,
            text_content=data,
        )
        if self._stack:
            parent_id = self._stack[-1]
            node.parent_id = parent_id
            self.nodes[parent_id].children.append(nid)
        self.nodes[nid] = node


# ---------------------------------------------------------------------------
# DOMSite — the site object
# ---------------------------------------------------------------------------

@dataclass
class DOMSite:
    """The site (topological space) of CSS selectors over a DOM tree.

    Nodes are the points; selectors define open sets.
    """

    nodes: dict[str, DOMNode] = field(default_factory=dict)
    root_id: str = ""

    # ---------------------------------------------------------------- factory

    @classmethod
    def from_html(cls, html_source: str) -> DOMSite:
        """Parse an HTML string into a ``DOMSite``."""
        parser = _HTMLParser()
        parser.feed(html_source)
        return cls(nodes=parser.nodes, root_id=parser.root_id)

    # -------------------------------------------------------- selector queries

    def nodes_matching(self, selector: str) -> list[DOMNode]:
        """Return all nodes matching *selector* (may be comma-separated)."""
        parts = SelectorParser._split_comma(selector)
        seen: set[str] = set()
        result: list[DOMNode] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            chain = SelectorParser.parse_selector(part)
            for node in self.nodes.values():
                if node.node_id in seen:
                    continue
                if SelectorParser.matches(chain, node, self):
                    seen.add(node.node_id)
                    result.append(node)
        return result

    def is_covering(self, selectors: list[str]) -> bool:
        """True if every ELEMENT node is matched by at least one selector."""
        elem_ids = {n.node_id for n in self.nodes.values()
                    if n.node_kind == DOMNodeKind.ELEMENT}
        for sel in selectors:
            for node in self.nodes_matching(sel):
                elem_ids.discard(node.node_id)
        return len(elem_ids) == 0

    def selector_overlap(self, s1: str, s2: str) -> list[DOMNode]:
        """Return nodes matching *both* s1 and s2."""
        ids1 = {n.node_id for n in self.nodes_matching(s1)}
        return [n for n in self.nodes_matching(s2) if n.node_id in ids1]

    # --------------------------------------------------------- tree traversal

    def children_of(self, node_id: str) -> list[DOMNode]:
        node = self.nodes.get(node_id)
        if node is None:
            return []
        return [self.nodes[cid] for cid in node.children if cid in self.nodes]

    def ancestors_of(self, node_id: str) -> list[DOMNode]:
        """Ordered from direct parent up to root."""
        result: list[DOMNode] = []
        nid = self.nodes.get(node_id)
        if nid is None:
            return result
        pid = nid.parent_id
        while pid and pid in self.nodes:
            result.append(self.nodes[pid])
            pid = self.nodes[pid].parent_id
        return result

    def subtree(self, node_id: str) -> list[DOMNode]:
        """BFS: node + all descendants."""
        result: list[DOMNode] = []
        queue = deque([node_id])
        while queue:
            nid = queue.popleft()
            node = self.nodes.get(nid)
            if node is None:
                continue
            result.append(node)
            queue.extend(node.children)
        return result

    def depth_of(self, node_id: str) -> int:
        """Depth from root (root = 0)."""
        d = 0
        nid = node_id
        while True:
            node = self.nodes.get(nid)
            if node is None or node.parent_id is None:
                break
            d += 1
            nid = node.parent_id
        return d

    def all_element_nodes(self) -> list[DOMNode]:
        return [n for n in self.nodes.values()
                if n.node_kind == DOMNodeKind.ELEMENT]

    # --------------------------------------------------------- serialisation

    def serialize(self) -> dict:
        return {
            "root_id": self.root_id,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
        }

    @classmethod
    def parse(cls, data: dict) -> DOMSite:
        nodes_raw = data.get("nodes", {})
        nodes = {nid: DOMNode.from_dict(d) for nid, d in nodes_raw.items()}
        return cls(nodes=nodes, root_id=data.get("root_id", ""))
