"""Accessibility obligations modelled as a presheaf over the DOM site.

Each WCAG 2.1 criterion is an obligation on specific DOM coordinates.
The obligation presheaf assigns, to each DOM open set (element coordinate),
the set of accessibility requirements that must be satisfied there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "WCAGLevel",
    "WCAGCriterion",
    "AccessibilityObligation",
    "AccessibilityChecker",
    "WCAGReport",
]


# ---------------------------------------------------------------------------
# 1. WCAGLevel
# ---------------------------------------------------------------------------

class WCAGLevel(str, Enum):
    A = "A"
    AA = "AA"
    AAA = "AAA"


# ---------------------------------------------------------------------------
# 2. WCAGCriterion
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WCAGCriterion:
    number: str
    name: str
    level: WCAGLevel
    applies_to: list[str]
    description: str

    @classmethod
    def wcag_21_criteria(cls) -> list[WCAGCriterion]:
        """Return ~20 key WCAG 2.1 criteria as a canonical list."""
        return [
            cls(
                number="1.1.1",
                name="Non-text Content",
                level=WCAGLevel.A,
                applies_to=["img", "input[type=image]", "canvas"],
                description=(
                    "All non-text content has a text alternative that serves "
                    "the equivalent purpose."
                ),
            ),
            cls(
                number="1.3.1",
                name="Info and Relationships",
                level=WCAGLevel.A,
                applies_to=["headings", "lists", "tables"],
                description=(
                    "Information, structure, and relationships conveyed through "
                    "presentation can be programmatically determined."
                ),
            ),
            cls(
                number="1.3.3",
                name="Sensory Characteristics",
                level=WCAGLevel.A,
                applies_to=["*"],
                description=(
                    "Instructions do not rely solely on sensory characteristics "
                    "such as shape, color, size, visual location, orientation, or sound."
                ),
            ),
            cls(
                number="1.4.1",
                name="Use of Color",
                level=WCAGLevel.A,
                applies_to=["*"],
                description=(
                    "Color is not used as the only visual means of conveying "
                    "information, indicating an action, prompting a response, "
                    "or distinguishing a visual element."
                ),
            ),
            cls(
                number="1.4.3",
                name="Contrast (Minimum)",
                level=WCAGLevel.AA,
                applies_to=["text", "images-of-text"],
                description=(
                    "Text and images of text have a contrast ratio of at least 4.5:1 "
                    "(3:1 for large text)."
                ),
            ),
            cls(
                number="1.4.4",
                name="Resize Text",
                level=WCAGLevel.AA,
                applies_to=["text"],
                description=(
                    "Text can be resized without assistive technology up to 200 percent "
                    "without loss of content or functionality."
                ),
            ),
            cls(
                number="1.4.10",
                name="Reflow",
                level=WCAGLevel.AA,
                applies_to=["layout"],
                description=(
                    "Content can be presented without loss of information or "
                    "functionality, and without requiring scrolling in two dimensions "
                    "at a CSS width equivalent to 320px."
                ),
            ),
            cls(
                number="1.4.11",
                name="Non-text Contrast",
                level=WCAGLevel.AA,
                applies_to=["ui-components", "graphical-objects"],
                description=(
                    "The visual presentation of UI components and graphical objects "
                    "has a contrast ratio of at least 3:1 against adjacent color(s)."
                ),
            ),
            cls(
                number="2.1.1",
                name="Keyboard",
                level=WCAGLevel.A,
                applies_to=["interactive"],
                description=(
                    "All functionality of the content is operable through a keyboard "
                    "interface without requiring specific timings for individual keystrokes."
                ),
            ),
            cls(
                number="2.1.2",
                name="No Keyboard Trap",
                level=WCAGLevel.A,
                applies_to=["interactive", "widget"],
                description=(
                    "If keyboard focus can be moved to a component using a keyboard "
                    "interface, then focus can be moved away from that component using "
                    "only a keyboard interface."
                ),
            ),
            cls(
                number="2.2.2",
                name="Pause, Stop, Hide",
                level=WCAGLevel.A,
                applies_to=["animation", "moving-content", "auto-updating"],
                description=(
                    "For any moving, blinking or scrolling information that starts "
                    "automatically, lasts more than five seconds, and is presented in "
                    "parallel with other content, there is a mechanism to pause, stop, "
                    "or hide it."
                ),
            ),
            cls(
                number="2.4.1",
                name="Bypass Blocks",
                level=WCAGLevel.A,
                applies_to=["navigation", "landmark"],
                description=(
                    "A mechanism is available to bypass blocks of content that are "
                    "repeated on multiple Web pages."
                ),
            ),
            cls(
                number="2.4.2",
                name="Page Titled",
                level=WCAGLevel.A,
                applies_to=["title"],
                description=(
                    "Web pages have titles that describe topic or purpose."
                ),
            ),
            cls(
                number="2.4.3",
                name="Focus Order",
                level=WCAGLevel.A,
                applies_to=["interactive", "focusable"],
                description=(
                    "If a Web page can be navigated sequentially and the navigation "
                    "sequences affect meaning or operation, focusable components receive "
                    "focus in an order that preserves meaning and operability."
                ),
            ),
            cls(
                number="2.4.6",
                name="Headings and Labels",
                level=WCAGLevel.AA,
                applies_to=["headings", "label"],
                description=(
                    "Headings and labels describe topic or purpose."
                ),
            ),
            cls(
                number="2.4.7",
                name="Focus Visible",
                level=WCAGLevel.AA,
                applies_to=["interactive", "focusable"],
                description=(
                    "Any keyboard operable user interface has a mode of operation "
                    "where the keyboard focus indicator is visible."
                ),
            ),
            cls(
                number="3.1.1",
                name="Language of Page",
                level=WCAGLevel.A,
                applies_to=["html"],
                description=(
                    "The default human language of each Web page can be "
                    "programmatically determined."
                ),
            ),
            cls(
                number="3.3.1",
                name="Error Identification",
                level=WCAGLevel.A,
                applies_to=["form", "input"],
                description=(
                    "If an input error is automatically detected, the item that is in "
                    "error is identified and the error is described to the user in text."
                ),
            ),
            cls(
                number="3.3.2",
                name="Labels or Instructions",
                level=WCAGLevel.A,
                applies_to=["form", "input", "select", "textarea"],
                description=(
                    "Labels or instructions are provided when content requires user input."
                ),
            ),
            cls(
                number="4.1.2",
                name="Name, Role, Value",
                level=WCAGLevel.A,
                applies_to=["ui-components", "interactive", "widget"],
                description=(
                    "For all user interface components, the name and role can be "
                    "programmatically determined; states, properties, and values that "
                    "can be set by the user can be programmatically set."
                ),
            ),
        ]


# ---------------------------------------------------------------------------
# 3. AccessibilityObligation
# ---------------------------------------------------------------------------

@dataclass
class AccessibilityObligation:
    """A single obligation: criterion applied to a specific DOM coordinate."""

    criterion: WCAGCriterion
    element_coord: str
    is_satisfied: bool = False
    detail: str = ""


# ---------------------------------------------------------------------------
# 4. AccessibilityChecker
# ---------------------------------------------------------------------------

class AccessibilityChecker:
    """Checks DOM elements against WCAG 2.1 obligations.

    Each check method returns a list of AccessibilityObligation instances,
    one per element examined, with ``is_satisfied`` set according to whether
    the element meets the relevant criterion.
    """

    def __init__(self) -> None:
        criteria_map = {c.number: c for c in WCAGCriterion.wcag_21_criteria()}
        self._criteria = criteria_map

    def _criterion(self, number: str) -> WCAGCriterion:
        return self._criteria[number]

    # ------------------------------------------------------------------
    # 4a. Images — criterion 1.1.1
    # ------------------------------------------------------------------

    def check_images(
        self,
        elements: list[tuple[str, dict]],
    ) -> list[AccessibilityObligation]:
        """Check that img elements carry an alt attribute (criterion 1.1.1).

        Parameters
        ----------
        elements:
            List of ``(coord_name, attrs)`` pairs, where *attrs* is a dict
            of HTML attribute names to values.
        """
        criterion = self._criterion("1.1.1")
        obligations: list[AccessibilityObligation] = []
        for coord, attrs in elements:
            has_alt = "alt" in attrs
            obligations.append(
                AccessibilityObligation(
                    criterion=criterion,
                    element_coord=coord,
                    is_satisfied=has_alt,
                    detail=(
                        "alt attribute present"
                        if has_alt
                        else "missing alt attribute — non-text content requires a text alternative"
                    ),
                )
            )
        return obligations

    # ------------------------------------------------------------------
    # 4b. Form labels — criterion 3.3.2
    # ------------------------------------------------------------------

    def check_form_labels(
        self,
        fields: list[tuple[str, str, dict]],
    ) -> list[AccessibilityObligation]:
        """Check that form inputs have an accessible label (criterion 3.3.2).

        A field is considered labelled if it has:
        - a ``label`` sibling (represented by ``_label`` key in attrs),
        - an ``aria-label`` attribute, or
        - an ``aria-labelledby`` attribute.

        Parameters
        ----------
        fields:
            List of ``(coord_name, tag, attrs)`` triples.
        """
        criterion = self._criterion("3.3.2")
        obligations: list[AccessibilityObligation] = []
        for coord, tag, attrs in fields:
            has_label = (
                "_label" in attrs
                or bool(attrs.get("aria-label", "").strip())
                or bool(attrs.get("aria-labelledby", "").strip())
                or bool(attrs.get("id") and attrs.get("_label_for"))
            )
            obligations.append(
                AccessibilityObligation(
                    criterion=criterion,
                    element_coord=coord,
                    is_satisfied=has_label,
                    detail=(
                        f"<{tag}> has accessible label"
                        if has_label
                        else (
                            f"<{tag}> is missing a label, aria-label, or aria-labelledby — "
                            "form inputs must have labels or instructions"
                        )
                    ),
                )
            )
        return obligations

    # ------------------------------------------------------------------
    # 4c. Heading structure — criterion 1.3.1
    # ------------------------------------------------------------------

    def check_heading_structure(
        self,
        headings: list[tuple[str, str]],
    ) -> list[AccessibilityObligation]:
        """Check for skipped heading levels (criterion 1.3.1).

        A skipped level means jumping from h1 → h3 without an h2 in between.
        Each problematic heading gets a violated obligation.  Headings that
        do not skip a level (or that are the very first heading) get a
        satisfied obligation.

        Parameters
        ----------
        headings:
            List of ``(coord_name, tag)`` pairs in DOM order,
            e.g. ``[("h1_1", "h1"), ("h2_1", "h2")]``.
        """
        criterion = self._criterion("1.3.1")
        obligations: list[AccessibilityObligation] = []

        _heading_re = re.compile(r"^h([1-6])$", re.IGNORECASE)

        prev_level: Optional[int] = None
        for coord, tag in headings:
            m = _heading_re.match(tag)
            if not m:
                continue
            level = int(m.group(1))
            if prev_level is None:
                satisfied = True
                detail = f"<{tag}> is the first heading"
            elif level <= prev_level + 1:
                satisfied = True
                detail = f"<{tag}> follows heading level {prev_level} — no skipped level"
            else:
                satisfied = False
                detail = (
                    f"<{tag}> skips from h{prev_level} to h{level} — "
                    "heading levels must not be skipped (violates 1.3.1)"
                )
            obligations.append(
                AccessibilityObligation(
                    criterion=criterion,
                    element_coord=coord,
                    is_satisfied=satisfied,
                    detail=detail,
                )
            )
            prev_level = level

        return obligations

    # ------------------------------------------------------------------
    # 4d. Language attribute — criterion 3.1.1
    # ------------------------------------------------------------------

    def check_lang_attr(
        self,
        html_attrs: dict,
    ) -> list[AccessibilityObligation]:
        """Check that the <html> element has a non-empty lang attribute (3.1.1)."""
        criterion = self._criterion("3.1.1")
        lang = html_attrs.get("lang", "")
        has_lang = bool(lang and lang.strip())
        return [
            AccessibilityObligation(
                criterion=criterion,
                element_coord="html",
                is_satisfied=has_lang,
                detail=(
                    f"lang=\"{lang}\" present on <html>"
                    if has_lang
                    else (
                        "<html> is missing a lang attribute — "
                        "the default human language must be programmatically determinable"
                    )
                ),
            )
        ]

    # ------------------------------------------------------------------
    # 4e. Page title — criterion 2.4.2
    # ------------------------------------------------------------------

    def check_page_title(
        self,
        title: Optional[str],
    ) -> list[AccessibilityObligation]:
        """Check that the page has a non-empty <title> (criterion 2.4.2)."""
        criterion = self._criterion("2.4.2")
        has_title = bool(title and title.strip())
        return [
            AccessibilityObligation(
                criterion=criterion,
                element_coord="title",
                is_satisfied=has_title,
                detail=(
                    f"Page title present: \"{title}\""
                    if has_title
                    else "Missing or empty <title> — web pages must have a descriptive title"
                ),
            )
        ]

    # ------------------------------------------------------------------
    # 4f. Focus order — criterion 2.4.3
    # ------------------------------------------------------------------

    def check_focus_order(
        self,
        tab_indices: list[tuple[str, Optional[int]]],
    ) -> list[AccessibilityObligation]:
        """Warn when positive tabindex values (>0) disrupt natural focus order (2.4.3).

        tabindex=0 (participates in natural order) and tabindex=-1 (programmatic
        focus only) are both acceptable.  Any tabindex > 0 creates a separate
        tab order and is flagged as a violation.

        Parameters
        ----------
        tab_indices:
            List of ``(coord_name, tabindex_value)`` pairs.  A value of
            ``None`` means the attribute is absent (natural order).
        """
        criterion = self._criterion("2.4.3")
        obligations: list[AccessibilityObligation] = []
        for coord, tabindex in tab_indices:
            if tabindex is not None and tabindex > 0:
                satisfied = False
                detail = (
                    f"tabindex={tabindex} on {coord} — positive tabindex values "
                    "override natural focus order and should be avoided (2.4.3)"
                )
            else:
                satisfied = True
                detail = (
                    f"tabindex={tabindex} on {coord} — acceptable focus order value"
                    if tabindex is not None
                    else f"{coord} has no explicit tabindex (natural order)"
                )
            obligations.append(
                AccessibilityObligation(
                    criterion=criterion,
                    element_coord=coord,
                    is_satisfied=satisfied,
                    detail=detail,
                )
            )
        return obligations

    # ------------------------------------------------------------------
    # 4g. Keyboard traps — criterion 2.1.2
    # ------------------------------------------------------------------

    def check_keyboard_traps(
        self,
        interactive_elements: list[str],
        focus_graph: dict[str, list[str]],
    ) -> list[AccessibilityObligation]:
        """Detect keyboard traps by searching for escape paths in the focus graph (2.1.2).

        An element is "trapped" if there is no path in *focus_graph* that leads
        away from it to any element outside its own strongly-connected component.
        In practice we check: starting from each interactive element, can we
        reach *any* element that is not itself?  If the element is isolated
        (only self-loops or only edges within a clique with no exit) it is trapped.

        We use a simple reachability DFS.  An element is considered to have an
        escape path if its reachable set (excluding itself) is non-empty.

        Parameters
        ----------
        interactive_elements:
            Coordinates of elements that can receive keyboard focus.
        focus_graph:
            Adjacency list mapping each coord to the coords reachable via Tab/Shift-Tab.
        """
        criterion = self._criterion("2.1.2")
        obligations: list[AccessibilityObligation] = []

        def reachable_from(start: str) -> set[str]:
            visited: set[str] = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                for neighbour in focus_graph.get(node, []):
                    if neighbour not in visited:
                        stack.append(neighbour)
            return visited

        for coord in interactive_elements:
            reachable = reachable_from(coord)
            # An escape exists if we can reach something OTHER than ourselves
            can_escape = bool(reachable - {coord})
            obligations.append(
                AccessibilityObligation(
                    criterion=criterion,
                    element_coord=coord,
                    is_satisfied=can_escape,
                    detail=(
                        f"{coord} has {len(reachable) - 1} reachable elements — no keyboard trap"
                        if can_escape
                        else (
                            f"{coord} is isolated in the focus graph — "
                            "keyboard focus cannot leave this element (keyboard trap, 2.1.2)"
                        )
                    ),
                )
            )
        return obligations

    # ------------------------------------------------------------------
    # 4h. Contrast — criterion 1.4.3
    # ------------------------------------------------------------------

    def check_contrast_wcag(
        self,
        pairs: list[tuple[str, float, float]],
    ) -> list[AccessibilityObligation]:
        """Check contrast ratios against WCAG 1.4.3 thresholds.

        Parameters
        ----------
        pairs:
            List of ``(element_coord, contrast_ratio, is_large_text)`` triples.
            *contrast_ratio* is the luminance contrast ratio (e.g. 4.5).
            *is_large_text* is True for text ≥18pt or ≥14pt bold.

        Thresholds (WCAG 1.4.3 AA):
        - Normal text: contrast ≥ 4.5:1
        - Large text:  contrast ≥ 3.0:1
        """
        criterion = self._criterion("1.4.3")
        obligations: list[AccessibilityObligation] = []
        for coord, ratio, is_large in pairs:
            threshold = 3.0 if is_large else 4.5
            text_type = "large text" if is_large else "normal text"
            satisfied = ratio >= threshold
            obligations.append(
                AccessibilityObligation(
                    criterion=criterion,
                    element_coord=coord,
                    is_satisfied=satisfied,
                    detail=(
                        f"{coord}: contrast {ratio:.2f}:1 meets {threshold:.1f}:1 threshold for {text_type}"
                        if satisfied
                        else (
                            f"{coord}: contrast {ratio:.2f}:1 is below {threshold:.1f}:1 required "
                            f"for {text_type} (1.4.3 AA)"
                        )
                    ),
                )
            )
        return obligations


# ---------------------------------------------------------------------------
# 5. WCAGReport
# ---------------------------------------------------------------------------

@dataclass
class WCAGReport:
    """Aggregated WCAG obligations for an entire page or component tree."""

    obligations: list[AccessibilityObligation] = field(default_factory=list)

    def satisfied(self) -> list[AccessibilityObligation]:
        """Return obligations that are satisfied."""
        return [o for o in self.obligations if o.is_satisfied]

    def violations(self) -> list[AccessibilityObligation]:
        """Return obligations that are *not* satisfied."""
        return [o for o in self.obligations if not o.is_satisfied]

    def by_level(self, level: WCAGLevel) -> list[AccessibilityObligation]:
        """Return all obligations (satisfied or not) at the given WCAG level."""
        return [o for o in self.obligations if o.criterion.level == level]

    def critical_violations(self) -> list[AccessibilityObligation]:
        """Return Level A violations — these must be fixed for basic conformance."""
        return [
            o for o in self.obligations
            if not o.is_satisfied and o.criterion.level == WCAGLevel.A
        ]

    def summary(self) -> str:
        """Return a human-readable summary of the report."""
        total = len(self.obligations)
        n_satisfied = len(self.satisfied())
        n_violations = len(self.violations())
        n_critical = len(self.critical_violations())

        level_a_total = len(self.by_level(WCAGLevel.A))
        level_aa_total = len(self.by_level(WCAGLevel.AA))
        level_aaa_total = len(self.by_level(WCAGLevel.AAA))

        lines = [
            "WCAG 2.1 Accessibility Report",
            "=" * 40,
            f"Total obligations : {total}",
            f"  Satisfied        : {n_satisfied}",
            f"  Violations       : {n_violations}",
            f"  Critical (A)     : {n_critical}",
            "",
            "Obligations by level:",
            f"  Level A   : {level_a_total}",
            f"  Level AA  : {level_aa_total}",
            f"  Level AAA : {level_aaa_total}",
        ]

        if n_critical:
            lines.append("")
            lines.append("Critical violations (Level A):")
            for o in self.critical_violations():
                lines.append(f"  [{o.criterion.number}] {o.element_coord} — {o.detail}")

        aa_violations = [
            o for o in self.violations() if o.criterion.level == WCAGLevel.AA
        ]
        if aa_violations:
            lines.append("")
            lines.append("Level AA violations:")
            for o in aa_violations:
                lines.append(f"  [{o.criterion.number}] {o.element_coord} — {o.detail}")

        return "\n".join(lines)
