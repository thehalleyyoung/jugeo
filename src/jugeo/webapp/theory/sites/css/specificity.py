"""
CSS Specificity and Cascade Ordering
=====================================

This module models the CSS cascade formally, distinguishing between two related
but distinct concepts:

**CSS Specificity as a partial order**
    Specificity is the triple (a, b, c) compared lexicographically.  It forms
    a *partial* order on *selectors*, but only a partial one in the sense that
    two distinct selectors can have equal specificity — neither beats the other
    by specificity alone.  When selectors share the same specificity the cascade
    must fall back to other criteria (origin, layer, source order).  Specificity
    itself therefore cannot break all ties; it is not a total order on the set
    of all declarations.

**The full cascade key as a total order**
    The ``CascadeKey`` composite (origin → layer → specificity → source_order)
    *is* a total order: every pair of declarations can be ranked unambiguously.
    Source order is a tiebreaker of last resort, and because no two declarations
    can occupy the same source position, equality is impossible.  This gives the
    cascade its deterministic, well-defined "winning" declaration.

References
----------
* CSS Cascading and Inheritance Level 5 — https://www.w3.org/TR/css-cascade-5/
* CSS Selectors Level 4 — https://www.w3.org/TR/selectors-4/#specificity-rules
"""

from __future__ import annotations

__all__ = [
    "CSSOrigin",
    "CSSLayerOrder",
    "CSSSpecificity",
    "CascadeKey",
    "CascadeSorter",
]

import re
from dataclasses import dataclass
from enum import Enum
from functools import total_ordering


# ---------------------------------------------------------------------------
# 1. CSSOrigin
# ---------------------------------------------------------------------------

class CSSOrigin(str, Enum):
    """The origin (and importance flag) of a CSS declaration.

    The CSS cascade defines a strict priority among origins.  Declarations from
    a higher-priority origin always win regardless of specificity.  The order
    from lowest to highest priority is:

    1. ``USER_AGENT``         — browser default styles
    2. ``USER_AGENT_IMPORTANT`` — browser ``!important`` (rarely used)
    3. ``USER``               — user stylesheet (e.g. accessibility overrides)
    4. ``AUTHOR``             — page stylesheet
    5. ``AUTHOR_IMPORTANT``   — page stylesheet with ``!important``
    6. ``USER_IMPORTANT``     — user stylesheet with ``!important``

    Note the reversal for ``!important``: author ``!important`` beats plain
    author, but user ``!important`` beats author ``!important``.  This allows
    users to enforce accessibility preferences that authors cannot override.
    """

    USER_AGENT = "user-agent"
    USER_AGENT_IMPORTANT = "user-agent-important"
    USER = "user"
    AUTHOR = "author"
    AUTHOR_IMPORTANT = "author-important"
    USER_IMPORTANT = "user-important"

    def numeric_priority(self) -> int:
        """Return an integer 0–5 reflecting cascade priority (higher wins).

        Examples
        --------
        >>> CSSOrigin.USER_AGENT.numeric_priority()
        0
        >>> CSSOrigin.USER_IMPORTANT.numeric_priority()
        5
        """
        return _ORIGIN_PRIORITY[self]

    def is_important(self) -> bool:
        """Return ``True`` if this origin carries an ``!important`` flag."""
        return self in (
            CSSOrigin.USER_AGENT_IMPORTANT,
            CSSOrigin.AUTHOR_IMPORTANT,
            CSSOrigin.USER_IMPORTANT,
        )


_ORIGIN_PRIORITY: dict[CSSOrigin, int] = {
    CSSOrigin.USER_AGENT: 0,
    CSSOrigin.USER_AGENT_IMPORTANT: 1,
    CSSOrigin.USER: 2,
    CSSOrigin.AUTHOR: 3,
    CSSOrigin.AUTHOR_IMPORTANT: 4,
    CSSOrigin.USER_IMPORTANT: 5,
}


# ---------------------------------------------------------------------------
# 2. CSSLayerOrder
# ---------------------------------------------------------------------------

@total_ordering
@dataclass
class CSSLayerOrder:
    """Position of a declaration within the ``@layer`` ordering.

    CSS Cascade 5 introduces *cascade layers*: ``@layer base { … }`` etc.
    Within the same origin, unlayered styles win over *all* layers, and among
    layers a later-declared layer beats an earlier one.

    Parameters
    ----------
    layer_name:
        The name of the ``@layer``, or ``None`` for unlayered declarations.
        ``None`` means the declaration sits outside any ``@layer`` block and
        therefore has the highest layer priority within its origin.
    layer_index:
        The zero-based index of this layer in declaration order.  Higher index
        means the layer was declared later and therefore wins over earlier ones.
        Ignored when ``layer_name`` is ``None`` (unlayered always wins).

    Notes
    -----
    Unlayered beats all named layers.  Among named layers, higher
    ``layer_index`` wins.  This gives a total order on layer positions within
    a single origin.
    """

    layer_name: str | None
    layer_index: int

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CSSLayerOrder):
            return NotImplemented
        if self.layer_name is None and other.layer_name is None:
            return True
        if self.layer_name is None or other.layer_name is None:
            return False
        return self.layer_index == other.layer_index

    def __lt__(self, other: object) -> bool:
        """Return ``True`` if *self* has lower cascade priority than *other*."""
        if not isinstance(other, CSSLayerOrder):
            return NotImplemented
        # unlayered is highest priority
        if self.layer_name is None:
            return False  # self is unlayered → never less than other
        if other.layer_name is None:
            return True   # other is unlayered → self is always less
        return self.layer_index < other.layer_index


# ---------------------------------------------------------------------------
# 3. CSSSpecificity
# ---------------------------------------------------------------------------

@dataclass
class CSSSpecificity:
    """The (a, b, c) specificity triple for a CSS selector.

    Specificity is defined in CSS Selectors Level 4 §16 as a three-component
    vector compared *lexicographically* (a first, then b, then c).

    Parameters
    ----------
    a : int
        Count of ID selectors (``#foo``).
    b : int
        Count of class selectors (``.bar``), attribute selectors (``[href]``),
        and most pseudo-classes (``:hover``, ``:nth-child()``, …).
    c : int
        Count of type selectors (``div``, ``span``) and pseudo-elements
        (``::before``, ``::first-line``).

    Why specificity is a *partial* order
    --------------------------------------
    Two selectors with identical (a, b, c) values are *incomparable* by
    specificity alone — neither beats the other.  For example ``div.foo``
    (0,1,1) and ``p[lang]`` (0,1,1) have equal specificity.  The cascade must
    therefore fall back to layer order and source order to decide the winner.
    Specificity is antisymmetric and transitive, satisfying the axioms of a
    partial order, but it is *not* total because the trichotomy law fails for
    equal-specificity pairs.
    """

    a: int
    b: int
    c: int

    # ------------------------------------------------------------------
    # Arithmetic
    # ------------------------------------------------------------------

    def __add__(self, other: CSSSpecificity) -> CSSSpecificity:
        """Component-wise addition (used when combining selector parts)."""
        return CSSSpecificity(self.a + other.a, self.b + other.b, self.c + other.c)

    # ------------------------------------------------------------------
    # Comparison — lexicographic on (a, b, c)
    # ------------------------------------------------------------------

    def _tuple(self) -> tuple[int, int, int]:
        return (self.a, self.b, self.c)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CSSSpecificity):
            return NotImplemented
        return self._tuple() < other._tuple()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, CSSSpecificity):
            return NotImplemented
        return self._tuple() <= other._tuple()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CSSSpecificity):
            return NotImplemented
        return self._tuple() == other._tuple()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, CSSSpecificity):
            return NotImplemented
        return self._tuple() > other._tuple()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, CSSSpecificity):
            return NotImplemented
        return self._tuple() >= other._tuple()

    def __hash__(self) -> int:
        return hash(self._tuple())

    def __repr__(self) -> str:
        return f"CSSSpecificity(a={self.a}, b={self.b}, c={self.c})"

    def __str__(self) -> str:
        return f"({self.a},{self.b},{self.c})"

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, selector: str) -> CSSSpecificity:
        """Parse a CSS selector string and return its specificity.

        Parsing rules (CSS Selectors Level 4 §16):

        * ``#id``                    → +1 to *a*
        * ``.class``, ``[attr]``     → +1 to *b*
        * ``:pseudo-class``          → +1 to *b* (except the forgiving
          pseudo-classes below)
        * ``tag``, ``::pseudo-element`` → +1 to *c*
        * ``*``                      → contributes 0,0,0
        * ``:is()``, ``:not()``, ``:has()``
                                     → specificity of the *most specific*
                                       argument list item
        * ``:where()``               → always 0,0,0

        Parameters
        ----------
        selector:
            A single compound or complex selector string, e.g.
            ``"#nav > .item:hover"`` or ``"div + p::first-line"``.

        Returns
        -------
        CSSSpecificity
            The computed (a, b, c) triple.

        Examples
        --------
        >>> CSSSpecificity.parse("#foo")
        CSSSpecificity(a=1, b=0, c=0)
        >>> CSSSpecificity.parse(".bar")
        CSSSpecificity(a=0, b=1, c=0)
        >>> CSSSpecificity.parse("#foo .bar div")
        CSSSpecificity(a=1, b=1, c=1)
        >>> CSSSpecificity.parse(":where(#foo)")
        CSSSpecificity(a=0, b=0, c=0)
        >>> CSSSpecificity.parse(":not(.a, #b)")
        CSSSpecificity(a=1, b=0, c=0)
        """
        return _parse_specificity(selector)


# -- Internal parsing helpers -----------------------------------------------

# Pseudo-classes whose specificity is determined by their argument list.
_FORGIVING_PSEUDO_CLASSES = {"is", "not", "has"}

# Pseudo-classes that always contribute zero specificity.
_ZERO_PSEUDO_CLASSES = {"where"}

# Known pseudo-elements (double-colon OR legacy single-colon).
_LEGACY_PSEUDO_ELEMENTS = {
    "first-line", "first-letter", "before", "after",
}


def _parse_specificity(selector: str) -> CSSSpecificity:
    """Recursive specificity parser."""
    result = CSSSpecificity(0, 0, 0)
    pos = 0
    text = selector.strip()
    n = len(text)

    while pos < n:
        ch = text[pos]

        # Skip combinators and whitespace
        if ch in " \t\n\r+>~|":
            pos += 1
            continue

        # Universal selector
        if ch == "*":
            pos += 1
            continue

        # ID selector
        if ch == "#":
            pos += 1
            pos = _skip_ident(text, pos)
            result = result + CSSSpecificity(1, 0, 0)
            continue

        # Class selector
        if ch == ".":
            pos += 1
            pos = _skip_ident(text, pos)
            result = result + CSSSpecificity(0, 1, 0)
            continue

        # Attribute selector
        if ch == "[":
            end = text.index("]", pos)
            pos = end + 1
            result = result + CSSSpecificity(0, 1, 0)
            continue

        # Pseudo-class or pseudo-element
        if ch == ":":
            pos += 1
            is_pseudo_element = False
            if pos < n and text[pos] == ":":
                is_pseudo_element = True
                pos += 1

            # Read the pseudo-class/element name
            name_start = pos
            while pos < n and (text[pos].isalnum() or text[pos] in "-_"):
                pos += 1
            name = text[name_start:pos].lower()

            if is_pseudo_element:
                result = result + CSSSpecificity(0, 0, 1)
                # Skip any functional arguments (unusual for pseudo-elements but be safe)
                if pos < n and text[pos] == "(":
                    pos = _skip_paren(text, pos)
                continue

            # Legacy single-colon pseudo-elements
            if name in _LEGACY_PSEUDO_ELEMENTS:
                result = result + CSSSpecificity(0, 0, 1)
                continue

            # :where() — zero specificity
            if name in _ZERO_PSEUDO_CLASSES:
                if pos < n and text[pos] == "(":
                    pos = _skip_paren(text, pos)
                continue

            # :is(), :not(), :has() — specificity of most-specific argument
            if name in _FORGIVING_PSEUDO_CLASSES:
                if pos < n and text[pos] == "(":
                    inner = _extract_paren_content(text, pos)
                    pos += len(inner) + 2  # skip '(' content ')'
                    best = _most_specific_of_arg_list(inner)
                    result = result + best
                continue

            # Regular pseudo-class
            if pos < n and text[pos] == "(":
                pos = _skip_paren(text, pos)
            result = result + CSSSpecificity(0, 1, 0)
            continue

        # Type selector (tag name) — must start with letter or underscore or '-'
        if ch.isalpha() or ch == "_" or ch == "-":
            pos = _skip_ident(text, pos)
            result = result + CSSSpecificity(0, 0, 1)
            continue

        # Anything else — skip
        pos += 1

    return result


def _skip_ident(text: str, pos: int) -> int:
    """Advance past an identifier (letters, digits, hyphens, underscores)."""
    n = len(text)
    while pos < n and (text[pos].isalnum() or text[pos] in "-_"):
        pos += 1
    return pos


def _skip_paren(text: str, pos: int) -> int:
    """Skip a balanced parenthesised block starting at *pos* (must be '(')."""
    depth = 0
    n = len(text)
    while pos < n:
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return pos


def _extract_paren_content(text: str, pos: int) -> str:
    """Return the content inside the parentheses starting at *pos*."""
    depth = 0
    start = pos + 1
    n = len(text)
    i = pos
    while i < n:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:]


def _most_specific_of_arg_list(args_str: str) -> CSSSpecificity:
    """Return the highest specificity among comma-separated selector arguments."""
    # Split on top-level commas only
    args = _split_top_level_commas(args_str)
    best = CSSSpecificity(0, 0, 0)
    for arg in args:
        s = _parse_specificity(arg.strip())
        if s > best:
            best = s
    return best


def _split_top_level_commas(text: str) -> list[str]:
    """Split *text* on commas that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


# ---------------------------------------------------------------------------
# 4. CascadeKey
# ---------------------------------------------------------------------------

@dataclass
class CascadeKey:
    """The total ordering key used by the CSS cascade algorithm.

    The cascade resolves conflicts between declarations targeting the same
    property on the same element.  Declarations are compared by the following
    criteria *in order*, with each criterion only consulted when the previous
    ones are equal:

    1. **Origin** (``CSSOrigin``) — user-agent < user < author < author!important
       < user!important
    2. **Layer** (``CSSLayerOrder``) — unlayered > later-declared layers >
       earlier-declared layers
    3. **Specificity** (``CSSSpecificity``) — lexicographic (a, b, c)
    4. **Source order** (``int``) — later declarations in the source win

    Why this is a *total* order
    ---------------------------
    Unlike specificity alone, the full ``CascadeKey`` is a total order.  The
    final tiebreaker — ``source_order`` — is unique per declaration (no two
    declarations occupy the same source position), so equality of the full key
    is impossible.  Every pair of declarations is therefore comparable, giving
    a well-defined unique winner.

    Parameters
    ----------
    origin:
        The origin (and importance) of the declaration.
    layer:
        The ``@layer`` position of the declaration within its origin.
    specificity:
        The selector specificity.
    source_order:
        The position of this declaration in document order (0-based; higher
        values appear later in the source and win ties).
    """

    origin: CSSOrigin
    layer: CSSLayerOrder
    specificity: CSSSpecificity
    source_order: int

    def _sort_key(self) -> tuple[int, CSSLayerOrder, tuple[int, int, int], int]:
        return (
            self.origin.numeric_priority(),
            self.layer,
            self.specificity._tuple(),
            self.source_order,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CascadeKey):
            return NotImplemented
        return self._cmp(other) < 0

    def __le__(self, other: object) -> bool:
        if not isinstance(other, CascadeKey):
            return NotImplemented
        return self._cmp(other) <= 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CascadeKey):
            return NotImplemented
        return self._cmp(other) == 0

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, CascadeKey):
            return NotImplemented
        return self._cmp(other) > 0

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, CascadeKey):
            return NotImplemented
        return self._cmp(other) >= 0

    def __hash__(self) -> int:
        return hash((self.origin, self.layer.layer_index, self.layer.layer_name,
                     self.specificity._tuple(), self.source_order))

    def _cmp(self, other: CascadeKey) -> int:
        """Return negative, zero, or positive for self < other comparisons."""
        # 1. Origin priority
        sp = self.origin.numeric_priority()
        op = other.origin.numeric_priority()
        if sp != op:
            return sp - op

        # 2. Layer order
        if self.layer < other.layer:
            return -1
        if self.layer > other.layer:
            return 1

        # 3. Specificity
        st = self.specificity._tuple()
        ot = other.specificity._tuple()
        if st < ot:
            return -1
        if st > ot:
            return 1

        # 4. Source order
        return self.source_order - other.source_order


# ---------------------------------------------------------------------------
# 5. CascadeSorter
# ---------------------------------------------------------------------------

class CascadeSorter:
    """Utility for sorting CSS declarations according to cascade priority.

    Given a list of ``(property_value, CascadeKey)`` pairs, ``CascadeSorter``
    applies the full cascade ordering to determine which declaration wins.

    The cascade winner is the declaration with the **highest** ``CascadeKey``
    (i.e. the last in the sorted order), matching the CSS specification's
    semantics: later-declared, higher-specificity, higher-origin declarations
    override earlier ones.

    Examples
    --------
    >>> sorter = CascadeSorter()
    >>> layer_a = CSSLayerOrder("base", 0)
    >>> layer_none = CSSLayerOrder(None, 0)
    >>> key_a = CascadeKey(CSSOrigin.AUTHOR, layer_a,
    ...                    CSSSpecificity(0, 1, 0), 0)
    >>> key_b = CascadeKey(CSSOrigin.AUTHOR, layer_none,
    ...                    CSSSpecificity(0, 0, 1), 1)
    >>> winner = sorter.winning_declaration([("red", key_a), ("blue", key_b)])
    >>> winner[0]
    'blue'
    """

    def sort_declarations(
        self,
        declarations: list[tuple[str, CascadeKey]],
    ) -> list[tuple[str, CascadeKey]]:
        """Sort *declarations* by cascade order, winning declaration first.

        Parameters
        ----------
        declarations:
            A list of ``(value, key)`` pairs, where *value* is an arbitrary
            string (e.g. the CSS property value) and *key* is the
            ``CascadeKey`` that governs its cascade priority.

        Returns
        -------
        list[tuple[str, CascadeKey]]
            The same list sorted so that the winning (highest-priority)
            declaration appears first (index 0) and the lowest-priority
            declaration appears last.
        """
        return sorted(declarations, key=lambda item: item[1], reverse=True)

    def winning_declaration(
        self,
        declarations: list[tuple[str, CascadeKey]],
    ) -> tuple[str, CascadeKey] | None:
        """Return the single winning declaration, or ``None`` if the list is empty.

        Parameters
        ----------
        declarations:
            A list of ``(value, key)`` pairs.

        Returns
        -------
        tuple[str, CascadeKey] | None
            The declaration with the highest ``CascadeKey``, or ``None`` if
            *declarations* is empty.
        """
        if not declarations:
            return None
        return max(declarations, key=lambda item: item[1])
