"""CSS box model as a fiber bundle over the DOM site.

Each DOM element carries a fiber of box-model data (content → padding →
border → margin).  Layout resolves the abstract CSS values into concrete
pixel dimensions.
"""

from __future__ import annotations

__all__ = [
    "BoxSizing",
    "BoxEdge",
    "CSSBox",
    "MarginCollapseAnalyzer",
    "ContainingBlock",
    "OverflowObstruction",
]

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# 1. BoxSizing
# ---------------------------------------------------------------------------

class BoxSizing(str, Enum):
    """Which edges are included in the declared *width* / *height* values."""

    CONTENT_BOX = "content-box"
    BORDER_BOX = "border-box"


# ---------------------------------------------------------------------------
# 2. BoxEdge
# ---------------------------------------------------------------------------

@dataclass
class BoxEdge:
    """The four sides of a single layer of the CSS box model.

    Used to represent padding, border widths, and margins independently.
    All values are in CSS pixels (non-negative for padding/border; margins
    may be negative).
    """

    top: float
    right: float
    bottom: float
    left: float

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def uniform(cls, value: float) -> BoxEdge:
        """Create a ``BoxEdge`` where all four sides share *value*."""
        return cls(top=value, right=value, bottom=value, left=value)

    @classmethod
    def zero(cls) -> BoxEdge:
        """Create a ``BoxEdge`` with all sides set to zero."""
        return cls(top=0.0, right=0.0, bottom=0.0, left=0.0)

    # ------------------------------------------------------------------
    # Dimension helpers
    # ------------------------------------------------------------------

    def horizontal(self) -> float:
        """Sum of the left and right sides."""
        return self.left + self.right

    def vertical(self) -> float:
        """Sum of the top and bottom sides."""
        return self.top + self.bottom


# ---------------------------------------------------------------------------
# 3. CSSBox
# ---------------------------------------------------------------------------

@dataclass
class CSSBox:
    """The full CSS box model for a single element.

    *content_width* is always stored as the **content-box** width regardless
    of the declared ``box-sizing`` property.  Use :meth:`with_box_sizing` to
    convert a specified width under a different ``box-sizing`` context into
    this canonical form.
    """

    content_width: float
    content_height: float | None
    padding: BoxEdge
    border: BoxEdge
    margin: BoxEdge
    box_sizing: BoxSizing = field(default=BoxSizing.CONTENT_BOX)

    # ------------------------------------------------------------------
    # Width calculations
    # ------------------------------------------------------------------

    def padding_box_width(self) -> float:
        """Content width plus horizontal padding."""
        return self.content_width + self.padding.horizontal()

    def border_box_width(self) -> float:
        """Content width plus horizontal padding and border."""
        return self.padding_box_width() + self.border.horizontal()

    def margin_box_width(self) -> float:
        """Full occupied horizontal extent including margins."""
        return self.border_box_width() + self.margin.horizontal()

    # ------------------------------------------------------------------
    # Height calculations (may be None when height is auto)
    # ------------------------------------------------------------------

    def padding_box_height(self) -> float | None:
        """Content height plus vertical padding, or *None* if height is auto."""
        if self.content_height is None:
            return None
        return self.content_height + self.padding.vertical()

    def border_box_height(self) -> float | None:
        """Content height plus vertical padding and border, or *None*."""
        pbh = self.padding_box_height()
        if pbh is None:
            return None
        return pbh + self.border.vertical()

    def margin_box_height(self) -> float | None:
        """Full occupied vertical extent including margins, or *None*."""
        bbh = self.border_box_height()
        if bbh is None:
            return None
        return bbh + self.margin.vertical()

    # ------------------------------------------------------------------
    # Box-sizing conversion
    # ------------------------------------------------------------------

    def with_box_sizing(
        self,
        sizing: BoxSizing,
        specified_width: float,
    ) -> CSSBox:
        """Return a new :class:`CSSBox` built from *specified_width* under *sizing*.

        CSS allows authors to declare ``width`` under either the
        ``content-box`` or ``border-box`` interpretation.  This method
        converts *specified_width* (interpreted according to *sizing*) into
        a new instance whose :attr:`content_width` is always the inner
        content width, making downstream calculations uniform.

        Parameters
        ----------
        sizing:
            The ``box-sizing`` value that governs how *specified_width* is
            interpreted by the author.
        specified_width:
            The declared width in CSS pixels.

        Returns
        -------
        CSSBox
            A new box whose :attr:`content_width` corresponds to the actual
            content area and whose :attr:`box_sizing` records the given
            *sizing*.
        """
        if sizing is BoxSizing.CONTENT_BOX:
            content_w = specified_width
        else:
            # border-box: specified width includes padding + border
            inset = self.padding.horizontal() + self.border.horizontal()
            # Clamp to zero — content width cannot be negative
            content_w = max(0.0, specified_width - inset)

        return CSSBox(
            content_width=content_w,
            content_height=self.content_height,
            padding=self.padding,
            border=self.border,
            margin=self.margin,
            box_sizing=sizing,
        )


# ---------------------------------------------------------------------------
# 4. MarginCollapseAnalyzer
# ---------------------------------------------------------------------------

class MarginCollapseAnalyzer:
    """Model CSS margin collapsing rules for block-level boxes.

    The CSS specification mandates that adjacent vertical margins *collapse*
    — i.e. they do **not** add but instead resolve to a single value — under
    several circumstances.  This class captures the three canonical collapse
    scenarios as pure functions.
    """

    # ------------------------------------------------------------------
    # Adjacent sibling collapse
    # ------------------------------------------------------------------

    @staticmethod
    def collapse_adjacent(
        margin_a_bottom: float,
        margin_b_top: float,
    ) -> float:
        """Collapse the bottom margin of element A with the top margin of B.

        When both values are positive the collapsed margin equals the
        larger.  When one or both values are negative the specification
        requires:

        * Identify the largest positive margin and the most negative margin.
        * If both are negative, take the most negative value.
        * Otherwise add the most negative value to the most positive value
          (which is equivalent to ``max_positive + min_negative`` where
          ``min_negative`` ≤ 0).

        Parameters
        ----------
        margin_a_bottom:
            Bottom margin of the preceding element (may be negative).
        margin_b_top:
            Top margin of the following element (may be negative).

        Returns
        -------
        float
            The single collapsed margin value.
        """
        a, b = margin_a_bottom, margin_b_top

        # Both positive — take the larger.
        if a >= 0 and b >= 0:
            return max(a, b)

        # Both negative — take the most negative (smallest absolute value
        # means least margin, but most negative means most shrinkage).
        if a <= 0 and b <= 0:
            return min(a, b)

        # Mixed signs: positive + negative.
        max_pos = max(a, b)
        min_neg = min(a, b)
        return max_pos + min_neg  # min_neg ≤ 0, so result ≤ max_pos

    # ------------------------------------------------------------------
    # Parent-child collapse
    # ------------------------------------------------------------------

    @staticmethod
    def collapse_parent_child(
        parent_margin_top: float,
        child_margin_top: float,
        has_border_or_padding: bool,
        has_block_fc: bool,
    ) -> float:
        """Resolve parent-child top margin collapsing.

        The first child's top margin collapses with its parent's top margin
        **only** when nothing separates them — i.e. the parent has no top
        border, no top padding, and does not establish a new block formatting
        context.  When collapse is suppressed the parent retains its own
        margin and the child its own.

        Parameters
        ----------
        parent_margin_top:
            The parent element's top margin.
        child_margin_top:
            The first child's top margin.
        has_border_or_padding:
            ``True`` if the parent has a non-zero top border or top padding,
            which prevents collapsing.
        has_block_fc:
            ``True`` if the parent establishes a new block formatting context
            (BFC), which also prevents collapsing.

        Returns
        -------
        float
            The effective top margin used for the parent in the current
            formatting context.  When collapse is suppressed this is simply
            *parent_margin_top* (the child keeps its own margin separately).
        """
        if has_border_or_padding or has_block_fc:
            # No collapse — parent and child each keep their own margins.
            return parent_margin_top

        # Collapse: the parent's top margin is replaced by the collapsed value.
        return MarginCollapseAnalyzer.collapse_adjacent(parent_margin_top, child_margin_top)

    # ------------------------------------------------------------------
    # Empty-block self-collapse
    # ------------------------------------------------------------------

    @staticmethod
    def collapse_empty_block(
        margin_top: float,
        margin_bottom: float,
    ) -> float:
        """Collapse an empty block's own top and bottom margins.

        An empty block (no border, padding, inline content, or clearance)
        collapses its top and bottom margins into a single value using the
        same algorithm as adjacent-sibling collapsing.

        Parameters
        ----------
        margin_top:
            The block's top margin.
        margin_bottom:
            The block's bottom margin.

        Returns
        -------
        float
            The single self-collapsed margin.
        """
        return MarginCollapseAnalyzer.collapse_adjacent(margin_top, margin_bottom)

    # ------------------------------------------------------------------
    # Block formatting context detection
    # ------------------------------------------------------------------

    @staticmethod
    def creates_bfc(
        display: str,
        overflow: str,
        float_val: str,
        position: str,
        contain: str,
    ) -> bool:
        """Determine whether CSS property values create a block formatting context.

        A block formatting context (BFC) is an isolated layout environment
        that prevents margin collapsing across its boundary and handles
        float containment.  The following conditions each independently
        trigger BFC creation per the CSS specification:

        * ``overflow`` is not ``visible`` (and not ``clip`` on all axes).
        * ``float`` is not ``none``.
        * ``position`` is ``absolute`` or ``fixed``.
        * ``display`` is ``inline-block``, ``flex``, ``inline-flex``,
          ``grid``, ``inline-grid``, ``flow-root``, ``table``,
          ``table-cell``, ``table-caption``, or any ``table-*`` variant.
        * ``contain`` includes ``layout``, ``paint``, ``strict``, or
          ``content``.

        Parameters
        ----------
        display:
            The computed ``display`` value (e.g. ``"flex"``, ``"block"``).
        overflow:
            The computed ``overflow`` shorthand value (e.g. ``"hidden"``).
            For simplicity both axes are assumed equal.
        float_val:
            The computed ``float`` value (``"none"``, ``"left"``, ``"right"``).
        position:
            The computed ``position`` value.
        contain:
            The computed ``contain`` value (space-separated token list).

        Returns
        -------
        bool
            ``True`` when the element creates a new BFC.
        """
        # overflow != visible creates a BFC (overflow:clip alone is nuanced
        # but we treat it as BFC-creating here for layout purposes).
        if overflow not in ("visible", ""):
            return True

        # Floated elements create their own BFC.
        if float_val not in ("none", ""):
            return True

        # Out-of-flow positioned elements.
        if position in ("absolute", "fixed"):
            return True

        # Specific display values that establish a BFC.
        bfc_displays = {
            "inline-block",
            "flex",
            "inline-flex",
            "grid",
            "inline-grid",
            "flow-root",
            "table",
            "table-cell",
            "table-caption",
            "table-row",
            "table-row-group",
            "table-header-group",
            "table-footer-group",
            "table-column",
            "table-column-group",
        }
        if display in bfc_displays:
            return True

        # CSS Containment: layout, paint, strict, and content all imply BFC.
        if contain:
            bfc_contain = {"layout", "paint", "strict", "content"}
            tokens = {t.strip() for t in contain.split()}
            if tokens & bfc_contain:
                return True

        return False


# ---------------------------------------------------------------------------
# 5. ContainingBlock
# ---------------------------------------------------------------------------

@dataclass
class ContainingBlock:
    """The reference box used to resolve percentage lengths on a child element.

    In CSS, percentage values for ``width``, ``margin``, ``padding``, etc.
    resolve against the *containing block* — the nearest block-level ancestor
    whose dimensions are known.  Height is special: when the containing
    block's height is ``auto`` (``None`` here), percentage heights on
    children cannot be resolved and remain ``auto`` themselves.
    """

    width: float
    height: float | None
    is_viewport: bool = field(default=False)
    establishes_bfc: bool = field(default=False)

    def resolve_percentage_width(self, pct: float) -> float:
        """Resolve *pct* (0–100) against the containing block's width.

        Both positive and negative percentages are supported (though negative
        percentages are invalid for ``width`` itself, they arise in margin
        calculations).

        Parameters
        ----------
        pct:
            A percentage value where ``100`` means 100 %.

        Returns
        -------
        float
            The resolved pixel value.
        """
        return self.width * pct / 100.0

    def resolve_percentage_height(self, pct: float) -> float | None:
        """Resolve *pct* (0–100) against the containing block's height.

        If the containing block's height is ``auto`` (``None``) this method
        returns ``None``, indicating the percentage cannot be resolved and
        the child's height should also be treated as ``auto``.

        Parameters
        ----------
        pct:
            A percentage value where ``100`` means 100 %.

        Returns
        -------
        float | None
            The resolved pixel value, or ``None`` if height is indeterminate.
        """
        if self.height is None:
            return None
        return self.height * pct / 100.0


# ---------------------------------------------------------------------------
# 6. OverflowObstruction
# ---------------------------------------------------------------------------

@dataclass
class OverflowObstruction:
    """Record of a box whose content exceeds its allocated space.

    In the fiber-bundle view of the DOM, each site (element) carries a fiber
    of layout data.  An ``OverflowObstruction`` is a *section* of that fiber
    that witnesses a mismatch between the intrinsic content size and the
    constrained box size, together with the CSS overflow properties that
    determine how the mismatch is handled.
    """

    element_coord: str
    """A CSS selector or DOM path identifying the element."""

    content_size: tuple[float, float]
    """Intrinsic (width, height) of the element's content in pixels."""

    box_size: tuple[float, float]
    """Allocated (width, height) of the border box in pixels."""

    overflow_x: str
    """Computed ``overflow-x`` value: visible | hidden | scroll | auto | clip."""

    overflow_y: str
    """Computed ``overflow-y`` value: visible | hidden | scroll | auto | clip."""

    creates_scroll: bool
    """Whether the element already has an active scroll container."""

    # ------------------------------------------------------------------
    # Obstruction predicate
    # ------------------------------------------------------------------

    def is_obstruction(self) -> bool:
        """Return ``True`` when content overflows *and* the overflow is clipped.

        An element is a *layout obstruction* when both conditions hold:

        1. At least one content dimension exceeds the corresponding box
           dimension.
        2. The relevant overflow property is ``hidden`` or ``clip``,
           meaning the overflowing content is invisible to the user and
           cannot be scrolled into view.

        Elements with ``overflow: visible`` are not obstructions because
        the overflowing content is still rendered (even if it intrudes into
        neighbouring boxes).  Elements with ``overflow: scroll`` or
        ``overflow: auto`` are not obstructions because the user can reach
        the clipped content via scrolling.

        Returns
        -------
        bool
            ``True`` if content is both larger than the box *and* invisibly
            clipped.
        """
        content_w, content_h = self.content_size
        box_w, box_h = self.box_size

        overflows_x = content_w > box_w
        overflows_y = content_h > box_h

        clipping_values = {"hidden", "clip"}

        x_clipped = overflows_x and self.overflow_x in clipping_values
        y_clipped = overflows_y and self.overflow_y in clipping_values

        return x_clipped or y_clipped
