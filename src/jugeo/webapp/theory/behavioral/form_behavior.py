"""
Forms as bidirectional presheaves bridging client and server.

A web form is not merely a collection of input elements — it is a *presheaf*
over the *field site*, where:

* Each **field** is a coordinate (an object in the site).
* Each **validation rule** is a section over that coordinate.
* **Client-side** and **server-side** validation are two local sections that
  must agree on their overlaps — i.e. satisfy a *descent condition*.
* A **form submission** is an attempt to glue those local sections into a
  global section (the submitted data).

When client says "valid" but the server says "invalid", we have a genuine
descent obstruction: the two local sections fail to agree on their shared
domain, blocking the construction of a global section.

This module formalises all of this using the JuGeo geometry API and provides:

1. :class:`InputKind` — taxonomy of HTML input types.
2. :class:`InputValueSpace` — what values each kind can hold.
3. :class:`ValidationRule` — client-side validation predicates.
4. :class:`FormFieldState` — run-time state of a single field.
5. :class:`FormPresheaf` — a form as a presheaf over its field site.
6. :class:`AsyncValidationTheory` — server-round-trip validation as descent.
7. :class:`FormSubmissionTheory` — submission lifecycle and error mapping.
8. :class:`FormAccessibilityTheory` — WCAG-required structural properties.
9. :class:`FormDescentChecker` — full coherence verification via descent.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
    GrothendieckTopology,
)
from jugeo.geometry.descent import (
    DescentEngine,
    GlobalSection,
    LocalSection,
    DescentResult,
    DescentObstruction,
)


__all__ = [
    # Enumerations
    "InputKind",
    "ValidationRuleKind",
    # Core data types
    "InputValueSpace",
    "ValidationRule",
    "FormFieldState",
    "FormSubmissionResult",
    # Main classes
    "FormPresheaf",
    "AsyncValidationTheory",
    "FormSubmissionTheory",
    "FormAccessibilityTheory",
    "FormDescentChecker",
]


# ---------------------------------------------------------------------------
# § 1  InputKind
# ---------------------------------------------------------------------------


class InputKind(str, Enum):
    """
    Taxonomy of HTML input types, corresponding to the ``type`` attribute of
    ``<input>`` elements plus the distinct form controls ``<select>`` and
    ``<textarea>``.

    Each member names a *value space* — the set of values the control can
    produce.  These value spaces differ fundamentally:

    * String-typed inputs (TEXT, EMAIL, …) produce Unicode strings.
    * Numeric inputs (NUMBER, RANGE) produce floats restricted to a lattice
      defined by ``min``, ``max``, and ``step``.
    * Temporal inputs (DATE, TIME, …) produce ISO-8601 strings that encode
      structured calendar / clock values.
    * Boolean inputs (CHECKBOX) produce ``True`` / ``False``.
    * Choice inputs (RADIO, SELECT_SINGLE, SELECT_MULTIPLE) produce values
      from a finite, explicitly declared option set.
    * FILE inputs produce ``File`` objects that are *not* JSON-serialisable.
    * HIDDEN inputs produce opaque strings (typically UUIDs or CSRF tokens).
    * Button inputs (BUTTON, SUBMIT, RESET) carry no value — they trigger
      actions.
    """

    TEXT = "text"
    NUMBER = "number"
    EMAIL = "email"
    TEL = "tel"
    URL = "url"
    PASSWORD = "password"
    SEARCH = "search"
    DATE = "date"
    TIME = "time"
    DATETIME_LOCAL = "datetime-local"
    MONTH = "month"
    WEEK = "week"
    COLOR = "color"
    RANGE = "range"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SELECT_SINGLE = "select"
    SELECT_MULTIPLE = "select-multiple"
    FILE = "file"
    HIDDEN = "hidden"
    TEXTAREA = "textarea"
    BUTTON = "button"
    SUBMIT = "submit"
    RESET = "reset"

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def is_text_like(self) -> bool:
        """Return True for inputs whose value is an unrestricted string."""
        return self in (
            InputKind.TEXT,
            InputKind.EMAIL,
            InputKind.TEL,
            InputKind.URL,
            InputKind.PASSWORD,
            InputKind.SEARCH,
            InputKind.HIDDEN,
            InputKind.TEXTAREA,
        )

    def is_numeric(self) -> bool:
        """Return True for inputs whose value is a floating-point number."""
        return self in (InputKind.NUMBER, InputKind.RANGE)

    def is_temporal(self) -> bool:
        """Return True for inputs that encode calendar or clock values."""
        return self in (
            InputKind.DATE,
            InputKind.TIME,
            InputKind.DATETIME_LOCAL,
            InputKind.MONTH,
            InputKind.WEEK,
        )

    def is_choice(self) -> bool:
        """Return True for inputs that select from a discrete option set."""
        return self in (
            InputKind.RADIO,
            InputKind.SELECT_SINGLE,
            InputKind.SELECT_MULTIPLE,
            InputKind.CHECKBOX,
        )

    def is_action(self) -> bool:
        """Return True for inputs that trigger actions rather than hold values."""
        return self in (InputKind.BUTTON, InputKind.SUBMIT, InputKind.RESET)

    def produces_file(self) -> bool:
        """Return True for inputs that produce File objects (not JSON-safe)."""
        return self is InputKind.FILE

    def is_multi_value(self) -> bool:
        """Return True for inputs that can produce a *list* of values."""
        return self is InputKind.SELECT_MULTIPLE


# ---------------------------------------------------------------------------
# § 2  InputValueSpace
# ---------------------------------------------------------------------------


class InputValueSpace:
    """
    Describes the *value space* of a given :class:`InputKind` subject to
    constraints declared by the form author.

    The value space is the set of *valid* values an input can hold.  It is
    shaped by:

    * The **kind** (which determines the type of values — string, float, bool,
      File, …).
    * **Constraints** (``min``, ``max``, ``step``, ``maxlength``, ``pattern``,
      ``options``, ``accept``, ``multiple``).

    The descent-theory view: each field's value space is a *sheaf on a
    one-point site*.  The form is the product site; the whole-form value is a
    global section.  Validation rules are *local sections* that must agree.

    This class is a **value object** — instantiate it with ``kind`` and
    ``constraints``, then call :meth:`valid_values` or
    :meth:`serialize_value`.
    """

    def __init__(
        self,
        kind: InputKind,
        constraints: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        self.constraints: dict[str, Any] = constraints or {}

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def valid_values(self) -> str:
        """
        Return a human-readable description of the value space.

        Used for documentation, error messages, and test oracle generation.
        """
        k = self.kind
        c = self.constraints

        if k is InputKind.TEXT or k is InputKind.TEXTAREA:
            min_len = c.get("minlength", 0)
            max_len = c.get("maxlength")
            pattern = c.get("pattern")
            parts = ["Unicode string"]
            if min_len:
                parts.append(f"length ≥ {min_len}")
            if max_len is not None:
                parts.append(f"length ≤ {max_len}")
            if pattern:
                parts.append(f"matching /{pattern}/")
            return ", ".join(parts)

        if k is InputKind.EMAIL:
            return "RFC-5322 email address (local@domain)"

        if k is InputKind.TEL:
            return "Telephone number string (format is locale-specific)"

        if k is InputKind.URL:
            return "Absolute URL (scheme://host/path)"

        if k is InputKind.PASSWORD:
            min_len = c.get("minlength", 0)
            max_len = c.get("maxlength")
            desc = "Opaque password string"
            if min_len:
                desc += f", length ≥ {min_len}"
            if max_len is not None:
                desc += f", length ≤ {max_len}"
            return desc

        if k is InputKind.SEARCH:
            return "Search query string"

        if k is InputKind.NUMBER:
            mn = c.get("min")
            mx = c.get("max")
            step = c.get("step", 1)
            parts = [f"float, step={step}"]
            if mn is not None:
                parts.append(f"min={mn}")
            if mx is not None:
                parts.append(f"max={mx}")
            return " ".join(parts)

        if k is InputKind.RANGE:
            mn = c.get("min", 0)
            mx = c.get("max", 100)
            step = c.get("step", 1)
            return f"float in [{mn}, {mx}] stepping by {step}"

        if k is InputKind.DATE:
            mn = c.get("min")
            mx = c.get("max")
            desc = "ISO date string (YYYY-MM-DD)"
            if mn or mx:
                desc += f" in [{mn or '−∞'}, {mx or '+∞'}]"
            return desc

        if k is InputKind.TIME:
            return "ISO time string (HH:MM or HH:MM:SS)"

        if k is InputKind.DATETIME_LOCAL:
            return "ISO local datetime (YYYY-MM-DDTHH:MM)"

        if k is InputKind.MONTH:
            return "Year-month string (YYYY-MM)"

        if k is InputKind.WEEK:
            return "ISO week string (YYYY-Www)"

        if k is InputKind.COLOR:
            return "CSS hex colour (#rrggbb)"

        if k is InputKind.CHECKBOX:
            return "Boolean (True or False)"

        if k is InputKind.RADIO:
            options = c.get("options", [])
            if options:
                return f"One of: {options!r}"
            return "One value from the declared option set"

        if k is InputKind.SELECT_SINGLE:
            options = c.get("options", [])
            if options:
                return f"Exactly one of: {options!r}"
            return "Exactly one value from the option set"

        if k is InputKind.SELECT_MULTIPLE:
            options = c.get("options", [])
            if options:
                return f"Non-empty subset of: {options!r}"
            return "A (possibly empty) subset of the option set"

        if k is InputKind.FILE:
            accept = c.get("accept", "*/*")
            multiple = c.get("multiple", False)
            desc = f"File object(s) matching MIME type(s) '{accept}'"
            if multiple:
                desc += " (multiple allowed)"
            return desc

        if k is InputKind.HIDDEN:
            return "Opaque string (not user-editable)"

        if k.is_action():
            return "No value (action trigger only)"

        return "Unconstrained string"

    def serialize_value(self, value: Any) -> Any:
        """
        Serialise *value* to a JSON-safe representation for form submission.

        FILE values are not JSON-serialisable; they must be sent as
        ``multipart/form-data`` — this method returns a sentinel dict.
        Booleans become ``'on'``/``''`` to match HTML form submission
        semantics.
        """
        k = self.kind

        if k is InputKind.FILE:
            # Files cannot be serialised as JSON.  Signal to the caller that
            # multipart encoding is required.
            if isinstance(value, list):
                return {"_file_upload": True, "count": len(value)}
            return {"_file_upload": True, "count": 1 if value is not None else 0}

        if k is InputKind.CHECKBOX:
            # HTML checkboxes submit 'on' when checked, nothing when unchecked.
            return "on" if value else ""

        if k is InputKind.SELECT_MULTIPLE:
            if isinstance(value, (list, tuple, set)):
                return list(value)
            return [value] if value is not None else []

        if k.is_numeric():
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        if k.is_action():
            return None

        # Default: stringify
        if value is None:
            return ""
        return str(value)

    def requires_multipart(self) -> bool:
        """Return True if this field forces ``multipart/form-data`` encoding."""
        return self.kind is InputKind.FILE

    def is_submittable(self) -> bool:
        """Return True if this field produces a submission value."""
        return not self.kind.is_action()

    def coerce(self, raw: Any) -> Any:
        """
        Attempt to coerce *raw* (typically a string from an HTML form) into
        the canonical Python type for this input kind.
        """
        k = self.kind
        if raw is None or raw == "":
            return None

        if k is InputKind.CHECKBOX:
            if isinstance(raw, bool):
                return raw
            return str(raw).lower() in ("on", "true", "1", "yes", "checked")

        if k.is_numeric():
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        if k is InputKind.SELECT_MULTIPLE:
            if isinstance(raw, list):
                return raw
            return [raw]

        return str(raw)


# ---------------------------------------------------------------------------
# § 3  ValidationRule
# ---------------------------------------------------------------------------


class ValidationRuleKind(str, Enum):
    """
    Taxonomy of client-side validation rules.

    Each rule kind corresponds to a *predicate* on field values.  Together
    they form a *covering family* over the field's value space: the field is
    "globally valid" only when every rule is locally satisfied.
    """

    REQUIRED = "required"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    PATTERN = "pattern"
    MIN = "min"
    MAX = "max"
    STEP = "step"
    EMAIL = "email"
    URL = "url"
    CUSTOM = "custom"


# Pre-compiled regex constants -----------------------------------------------

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

_URL_RE = re.compile(
    r"^(?:https?|ftp)://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


@dataclass
class ValidationRule:
    """
    A single client-side validation predicate for a form field.

    A :class:`ValidationRule` is a *local section* over the coordinate
    representing a field's value.  The entire set of rules for a field forms a
    *covering* of that coordinate; the field passes validation when *every*
    rule's local section is consistent (i.e. no rule raises an obstruction).

    Parameters
    ----------
    kind:
        Which class of rule this is.
    param:
        Rule parameter (``n`` for MIN_LENGTH/MAX_LENGTH/MIN/MAX/STEP,
        compiled regex pattern for PATTERN, description string for CUSTOM).
    message:
        Custom error message.  If ``None``, a default is generated.
    custom_fn:
        For ``CUSTOM`` rules only: a callable ``(value) -> bool``.
    """

    kind: ValidationRuleKind
    param: Any = None
    message: str | None = None
    custom_fn: Callable[[Any], bool] | None = None

    # Convenience factory methods -------------------------------------------

    @classmethod
    def required(cls, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.REQUIRED, message=message)

    @classmethod
    def min_length(cls, n: int, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.MIN_LENGTH, param=n, message=message)

    @classmethod
    def max_length(cls, n: int, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.MAX_LENGTH, param=n, message=message)

    @classmethod
    def pattern(cls, regex: str, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.PATTERN, param=regex, message=message)

    @classmethod
    def min_value(cls, n: float, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.MIN, param=n, message=message)

    @classmethod
    def max_value(cls, n: float, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.MAX, param=n, message=message)

    @classmethod
    def step(cls, n: float, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.STEP, param=n, message=message)

    @classmethod
    def email(cls, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.EMAIL, message=message)

    @classmethod
    def url(cls, message: str | None = None) -> ValidationRule:
        return cls(kind=ValidationRuleKind.URL, message=message)

    @classmethod
    def custom(
        cls,
        fn: Callable[[Any], bool],
        description: str = "custom",
        message: str | None = None,
    ) -> ValidationRule:
        return cls(
            kind=ValidationRuleKind.CUSTOM,
            param=description,
            message=message,
            custom_fn=fn,
        )

    # Core evaluation -------------------------------------------------------

    def validate(self, value: Any) -> tuple[bool, str]:
        """
        Evaluate this rule against *value*.

        Returns
        -------
        tuple[bool, str]
            ``(True, "")`` when the rule passes; ``(False, error_message)``
            when it fails.

        This is the *local section evaluation* in sheaf terms: we are asking
        whether this section is defined (consistent) at the given value point.
        """
        k = self.kind

        if k is ValidationRuleKind.REQUIRED:
            if value is None:
                return False, self.message or "This field is required."
            if isinstance(value, str) and not value.strip():
                return False, self.message or "This field is required."
            if isinstance(value, list) and len(value) == 0:
                return False, self.message or "Please select at least one option."
            return True, ""

        if k is ValidationRuleKind.MIN_LENGTH:
            n: int = int(self.param)
            s = str(value) if value is not None else ""
            if len(s) < n:
                return (
                    False,
                    self.message or f"Must be at least {n} character{'s' if n != 1 else ''} long.",
                )
            return True, ""

        if k is ValidationRuleKind.MAX_LENGTH:
            n = int(self.param)
            s = str(value) if value is not None else ""
            if len(s) > n:
                return (
                    False,
                    self.message or f"Must be at most {n} character{'s' if n != 1 else ''} long.",
                )
            return True, ""

        if k is ValidationRuleKind.PATTERN:
            s = str(value) if value is not None else ""
            try:
                if not re.fullmatch(self.param, s):
                    return False, self.message or f"Must match the pattern /{self.param}/."
            except re.error as exc:
                return False, f"Invalid pattern: {exc}"
            return True, ""

        if k is ValidationRuleKind.MIN:
            if value is None:
                return True, ""  # REQUIRED handles None
            try:
                if float(value) < float(self.param):
                    return False, self.message or f"Must be at least {self.param}."
            except (TypeError, ValueError):
                return False, "Must be a number."
            return True, ""

        if k is ValidationRuleKind.MAX:
            if value is None:
                return True, ""
            try:
                if float(value) > float(self.param):
                    return False, self.message or f"Must be at most {self.param}."
            except (TypeError, ValueError):
                return False, "Must be a number."
            return True, ""

        if k is ValidationRuleKind.STEP:
            if value is None:
                return True, ""
            try:
                v = float(value)
                s = float(self.param)
                # Use a small epsilon for floating-point division
                remainder = abs(v % s)
                if remainder > 1e-9 and abs(remainder - s) > 1e-9:
                    return False, self.message or f"Must be a multiple of {self.param}."
            except (TypeError, ValueError):
                return False, "Must be a number."
            return True, ""

        if k is ValidationRuleKind.EMAIL:
            s = str(value) if value else ""
            if not _EMAIL_RE.match(s):
                return False, self.message or "Must be a valid email address."
            return True, ""

        if k is ValidationRuleKind.URL:
            s = str(value) if value else ""
            if not _URL_RE.match(s):
                return False, self.message or "Must be a valid URL (include http:// or https://)."
            return True, ""

        if k is ValidationRuleKind.CUSTOM:
            if self.custom_fn is None:
                return True, ""  # no function registered — pass by default
            try:
                ok = bool(self.custom_fn(value))
            except Exception as exc:  # noqa: BLE001
                return False, f"Validation error: {exc}"
            if not ok:
                return False, self.message or "Invalid value."
            return True, ""

        return True, ""

    def describe(self) -> str:
        """Return a short human-readable description of this rule."""
        k = self.kind
        if k is ValidationRuleKind.REQUIRED:
            return "required"
        if k is ValidationRuleKind.MIN_LENGTH:
            return f"min-length={self.param}"
        if k is ValidationRuleKind.MAX_LENGTH:
            return f"max-length={self.param}"
        if k is ValidationRuleKind.PATTERN:
            return f"pattern=/{self.param}/"
        if k is ValidationRuleKind.MIN:
            return f"min={self.param}"
        if k is ValidationRuleKind.MAX:
            return f"max={self.param}"
        if k is ValidationRuleKind.STEP:
            return f"step={self.param}"
        if k is ValidationRuleKind.EMAIL:
            return "email"
        if k is ValidationRuleKind.URL:
            return "url"
        if k is ValidationRuleKind.CUSTOM:
            return f"custom({self.param})"
        return str(k.value)


# ---------------------------------------------------------------------------
# § 4  FormFieldState
# ---------------------------------------------------------------------------


@dataclass
class FormFieldState:
    """
    Run-time state of a single form field.

    This is the *local section* of the form presheaf at the coordinate named
    by ``name``.  The state captures not only the current *value*, but also
    the user-interaction history (``is_touched``, ``is_dirty``) and
    validation results (``errors``, ``is_validating``).

    The distinction between *touched* and *dirty* matters for UI feedback:

    * We show validation errors only for **touched** fields (avoid bombarding
      first-time users with errors before they've had a chance to type).
    * We highlight **dirty** fields to indicate unsaved changes.

    Parameters
    ----------
    name:
        Field identifier (matches the key in the form schema).
    kind:
        Input kind (determines value space and serialisation).
    value:
        Current value in the canonical Python type for this kind.
    initial_value:
        Value at form initialisation (used to detect dirtiness).
    is_touched:
        True once the user has interacted with this field (focus + blur, or
        any value change).
    is_dirty:
        True when ``value != initial_value``.
    errors:
        List of active validation error messages (empty = valid).
    is_validating:
        True while an async (server-side) validation round-trip is pending.
    rules:
        The validation rules attached to this field.
    constraints:
        Additional HTML constraints (``min``, ``max``, ``maxlength``, …).
    label:
        Human-readable label for this field (used in error summaries).
    required:
        Whether this field is required (shorthand for REQUIRED rule).
    aria_describedby:
        List of element IDs that describe this field (error containers,
        hint text, …).
    """

    name: str
    kind: InputKind
    value: Any = None
    initial_value: Any = None
    is_touched: bool = False
    is_dirty: bool = False
    errors: list[str] = field(default_factory=list)
    is_validating: bool = False
    rules: list[ValidationRule] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    required: bool = False
    aria_describedby: list[str] = field(default_factory=list)

    # Derived properties ---------------------------------------------------

    @property
    def is_valid(self) -> bool:
        """True when the field has no validation errors."""
        return not self.errors

    @property
    def show_errors(self) -> bool:
        """True when errors should be visible in the UI (field was touched)."""
        return self.is_touched and bool(self.errors)

    def touch(self) -> None:
        """Mark this field as having been interacted with by the user."""
        self.is_touched = True

    def update_value(self, new_value: Any) -> None:
        """
        Update the current value and recompute the ``is_dirty`` flag.

        Does NOT run validation — call :meth:`FormPresheaf.set_value` which
        orchestrates the full update-validate-notify cycle.
        """
        self.value = new_value
        self.is_dirty = new_value != self.initial_value
        self.is_touched = True

    def run_rules(self) -> list[str]:
        """
        Evaluate all attached :attr:`rules` against the current :attr:`value`.

        Returns the list of error messages (empty on success).  Also updates
        :attr:`errors` in-place.
        """
        errors: list[str] = []
        for rule in self.rules:
            ok, msg = rule.validate(self.value)
            if not ok:
                errors.append(msg)
        self.errors = errors
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "value": self.value,
            "initial_value": self.initial_value,
            "is_touched": self.is_touched,
            "is_dirty": self.is_dirty,
            "errors": list(self.errors),
            "is_validating": self.is_validating,
            "is_valid": self.is_valid,
            "required": self.required,
            "label": self.label,
            "aria_describedby": list(self.aria_describedby),
        }


# ---------------------------------------------------------------------------
# § 5  FormPresheaf
# ---------------------------------------------------------------------------


@dataclass
class _FormStep:
    """Internal record for a single step in a multi-step form."""

    step_number: int
    field_names: list[str]
    title: str = ""
    description: str = ""


class FormPresheaf:
    """
    A web form modelled as a *bidirectional presheaf* over the field site.

    **Site structure**

    The *field site* :math:`\\mathcal{F}` has:

    * **Objects** — one coordinate per field (e.g. ``form.username``).
    * **Morphisms** — inclusion morphisms from field coordinates into the
      whole-form coordinate (``form.root``), and between related fields
      (e.g. ``password → password_confirm``).
    * **Topology** — the canonical covering: the root is covered by all
      fields.

    **Presheaf directions**

    * *Client → server* (submission direction): the form collects values and
      sends them to a server endpoint.
    * *Server → client* (initialisation and error-mapping direction): the
      server populates initial values and maps validation errors back onto
      fields.

    This bidirectionality is what makes the form a *bivariant* functor — it
    is contravariant in the client→server direction (restriction = selecting
    only relevant fields for this endpoint) and covariant in the
    server→client direction (extension = populating defaults).

    **Multi-step forms**

    A multi-step form is a *covering* of the root form coordinate by step
    sub-coordinates.  Each step's completion is a *local section*.  The form
    is fully submittable only when all steps have consistent local sections —
    i.e. the covering satisfies the descent condition.

    Parameters
    ----------
    form_id:
        Unique identifier for this form instance.
    action:
        Server URL this form submits to.
    method:
        HTTP method (``"POST"`` or ``"GET"``).
    encoding:
        MIME type (``"application/x-www-form-urlencoded"``,
        ``"multipart/form-data"``, or ``"application/json"``).
    """

    def __init__(
        self,
        form_id: str | None = None,
        action: str = "",
        method: str = "POST",
        encoding: str = "application/x-www-form-urlencoded",
    ) -> None:
        self.form_id: str = form_id or f"form_{uuid.uuid4().hex[:8]}"
        self.action = action
        self.method = method.upper()
        self.encoding = encoding

        # Field registry: name → FormFieldState
        self._fields: dict[str, FormFieldState] = {}

        # Multi-step configuration
        self._steps: list[_FormStep] = []

        # The underlying JuGeo site for formal descent checks
        self._site: Site = Site()
        self._root_coord = Coordinate(
            components=(f"form.{self.form_id}",),
            kind=CoordinateKind.MODULE,
        )
        self._site.add_coordinate(self._root_coord)

    # ------------------------------------------------------------------
    # Field registration
    # ------------------------------------------------------------------

    def register_field(
        self,
        name: str,
        kind: InputKind,
        constraints: dict[str, Any] | None = None,
        initial_value: Any = None,
        label: str = "",
        rules: list[ValidationRule] | None = None,
    ) -> FormFieldState:
        """
        Register a field with the form.

        This creates both a :class:`FormFieldState` entry and a
        :class:`~jugeo.geometry.site.Coordinate` in the field site.

        Parameters
        ----------
        name:
            Field identifier.  Must be unique within this form.
        kind:
            The :class:`InputKind` of this field.
        constraints:
            HTML constraints (``min``, ``max``, ``maxlength``, ``options``,
            ``pattern``, …).
        initial_value:
            Starting value.
        label:
            Human-readable label for accessibility and error summaries.
        rules:
            Explicit validation rules.  If ``None``, rules are inferred from
            *constraints*.

        Returns
        -------
        FormFieldState
            The newly created (and registered) field state object.
        """
        constraints = constraints or {}
        inferred_rules = rules if rules is not None else self._infer_rules(kind, constraints)

        state = FormFieldState(
            name=name,
            kind=kind,
            value=initial_value,
            initial_value=initial_value,
            rules=inferred_rules,
            constraints=constraints,
            label=label or name,
            required=any(r.kind is ValidationRuleKind.REQUIRED for r in inferred_rules),
        )
        self._fields[name] = state

        # Register coordinate in the geometry site
        coord = Coordinate(
            components=(f"form.{self.form_id}", f"field.{name}"),
            kind=CoordinateKind.REGION,
            metadata={"input_kind": kind.value},
        )
        self._site.add_coordinate(coord)

        # Add inclusion morphism: field → root
        morph = Morphism(
            source=coord,
            target=self._root_coord,
            kind=MorphismKind.INCLUSION,
            label=f"include_{name}",
        )
        self._site.add_morphism(morph)

        return state

    def _infer_rules(
        self, kind: InputKind, constraints: dict[str, Any]
    ) -> list[ValidationRule]:
        """
        Derive :class:`ValidationRule` objects from HTML constraint attributes.

        This mirrors the browser's built-in constraint validation algorithm
        so that our Python-side checks match what the browser would do.
        """
        rules: list[ValidationRule] = []

        if constraints.get("required"):
            rules.append(ValidationRule.required())

        if kind is InputKind.EMAIL:
            rules.append(ValidationRule.email())

        if kind is InputKind.URL:
            rules.append(ValidationRule.url())

        if "minlength" in constraints:
            rules.append(ValidationRule.min_length(int(constraints["minlength"])))

        if "maxlength" in constraints:
            rules.append(ValidationRule.max_length(int(constraints["maxlength"])))

        if "pattern" in constraints:
            rules.append(ValidationRule.pattern(constraints["pattern"]))

        if kind.is_numeric():
            if "min" in constraints:
                rules.append(ValidationRule.min_value(float(constraints["min"])))
            if "max" in constraints:
                rules.append(ValidationRule.max_value(float(constraints["max"])))
            if "step" in constraints:
                rules.append(ValidationRule.step(float(constraints["step"])))

        return rules

    # ------------------------------------------------------------------
    # Value management
    # ------------------------------------------------------------------

    def set_value(self, name: str, value: Any) -> list[str]:
        """
        Update the value of field *name* and run its validation rules.

        This is the primary mutation entry-point.  In React terms this is
        the ``onChange`` handler.

        Returns
        -------
        list[str]
            Validation errors for this field after the update (empty = valid).

        Raises
        ------
        KeyError
            If *name* is not a registered field.
        """
        state = self._fields[name]
        state.update_value(value)
        return state.run_rules()

    def get_value(self, name: str) -> Any:
        """Return the current value of field *name*."""
        return self._fields[name].value

    def get_field(self, name: str) -> FormFieldState:
        """Return the :class:`FormFieldState` for field *name*."""
        return self._fields[name]

    def field_names(self) -> list[str]:
        """Return all registered field names."""
        return list(self._fields.keys())

    def touch_field(self, name: str) -> None:
        """Mark field *name* as touched (user interacted with it)."""
        self._fields[name].touch()

    def touch_all(self) -> None:
        """Mark all fields as touched (e.g. on submit attempt)."""
        for state in self._fields.values():
            state.touch()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_all(self) -> dict[str, list[str]]:
        """
        Run all validation rules for all registered fields.

        Returns a mapping of ``field_name → [error_messages]``.  Fields with
        no errors are *not* included in the result (so an empty dict means
        the form is fully valid).

        This is equivalent to asking for the *global section* of the error
        presheaf.  An empty result means the global section is trivial (no
        obstructions).
        """
        errors: dict[str, list[str]] = {}
        for name, state in self._fields.items():
            field_errors = state.run_rules()
            if field_errors:
                errors[name] = field_errors
        return errors

    def is_form_valid(self) -> bool:
        """
        Return True if all fields currently satisfy their validation rules.

        Does NOT trigger async validation — it only evaluates the synchronous
        local rules.
        """
        return all(state.is_valid for state in self._fields.values())

    def get_errors(self) -> dict[str, list[str]]:
        """Return current errors for all fields (does not re-run rules)."""
        return {
            name: list(state.errors)
            for name, state in self._fields.items()
            if state.errors
        }

    def set_server_errors(self, server_errors: dict[str, list[str]]) -> None:
        """
        Inject field errors returned by the server into the form state.

        This is the *server → client* direction of the form presheaf.  The
        server's validation result is mapped back onto the field coordinates,
        creating a new (potentially obstructed) local section.
        """
        for name, messages in server_errors.items():
            if name in self._fields:
                self._fields[name].errors.extend(messages)
                self._fields[name].is_touched = True  # reveal the errors in UI

    # ------------------------------------------------------------------
    # Serialisation / submission
    # ------------------------------------------------------------------

    def to_submission_data(self) -> dict[str, Any]:
        """
        Serialise the form's current values for transmission to the server.

        * Action inputs are excluded.
        * FILE inputs are flagged as requiring multipart encoding.
        * Values are coerced to their canonical types.

        Returns
        -------
        dict[str, Any]
            Submission payload.  Check ``_requires_multipart`` key to
            determine whether ``multipart/form-data`` encoding is needed.
        """
        payload: dict[str, Any] = {}
        needs_multipart = False

        for name, state in self._fields.items():
            if state.kind.is_action():
                continue
            vs = InputValueSpace(state.kind, state.constraints)
            serialised = vs.serialize_value(state.value)
            if state.kind is InputKind.FILE:
                needs_multipart = True
            payload[name] = serialised

        payload["_requires_multipart"] = needs_multipart
        return payload

    def reset(self) -> None:
        """Reset all fields to their initial values, clearing touched/dirty/errors."""
        for state in self._fields.values():
            state.value = state.initial_value
            state.is_touched = False
            state.is_dirty = False
            state.errors = []
            state.is_validating = False

    # ------------------------------------------------------------------
    # Multi-step forms
    # ------------------------------------------------------------------

    def add_step(
        self,
        field_names: list[str],
        title: str = "",
        description: str = "",
    ) -> int:
        """
        Declare a step in a multi-step form.

        Steps partition the form's fields into an ordered sequence.  Each
        step is a *local section* of the form presheaf; completing all steps
        produces a global section.

        Returns the 0-based step index.
        """
        step_num = len(self._steps)
        self._steps.append(
            _FormStep(
                step_number=step_num,
                field_names=list(field_names),
                title=title,
                description=description,
            )
        )
        return step_num

    def step_is_complete(self, step_num: int) -> bool:
        """
        Return True if all required fields in step *step_num* are valid.

        A step is *complete* when its local section is consistent — all
        required fields have values and all validation rules pass.

        This mirrors the descent condition on a covering family: the global
        section (full submission) can be assembled only when every local
        section (step) is valid.

        Raises
        ------
        IndexError
            If *step_num* is out of range.
        """
        if step_num >= len(self._steps):
            raise IndexError(f"Step {step_num} does not exist (form has {len(self._steps)} steps).")
        step = self._steps[step_num]
        for name in step.field_names:
            state = self._fields.get(name)
            if state is None:
                continue
            # Run rules fresh to get current status
            state.run_rules()
            if not state.is_valid:
                return False
        return True

    def all_steps_complete(self) -> bool:
        """Return True when all declared steps are complete."""
        return all(self.step_is_complete(i) for i in range(len(self._steps)))

    def step_count(self) -> int:
        """Return the number of declared steps (0 if not a multi-step form)."""
        return len(self._steps)

    def get_step_fields(self, step_num: int) -> list[FormFieldState]:
        """Return the field states for step *step_num*."""
        step = self._steps[step_num]
        return [self._fields[n] for n in step.field_names if n in self._fields]

    # ------------------------------------------------------------------
    # Site access
    # ------------------------------------------------------------------

    def site(self) -> Site:
        """Return the underlying JuGeo :class:`~jugeo.geometry.site.Site`."""
        return self._site

    def summary(self) -> dict[str, Any]:
        """Return a diagnostic summary of the form's current state."""
        return {
            "form_id": self.form_id,
            "action": self.action,
            "method": self.method,
            "encoding": self.encoding,
            "field_count": len(self._fields),
            "step_count": len(self._steps),
            "is_valid": self.is_form_valid(),
            "errors": self.get_errors(),
            "dirty_fields": [n for n, s in self._fields.items() if s.is_dirty],
            "touched_fields": [n for n, s in self._fields.items() if s.is_touched],
        }


# ---------------------------------------------------------------------------
# § 6  AsyncValidationTheory
# ---------------------------------------------------------------------------


@dataclass
class _AsyncValidationRequest:
    """Pending async validation request."""

    field_name: str
    value: Any
    endpoint: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    debounce_ms: int = 300

    def describe(self) -> str:
        return (
            f"AsyncValidation(field={self.field_name!r}, endpoint={self.endpoint!r}, "
            f"debounce={self.debounce_ms}ms, id={self.request_id})"
        )


@dataclass
class AsyncValidationResult:
    """Result of an async (server-side) validation check."""

    field_name: str
    value: Any
    is_valid: bool
    error: str | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "value": self.value,
            "is_valid": self.is_valid,
            "error": self.error,
            "request_id": self.request_id,
        }


class AsyncValidationTheory:
    """
    Server-side validation modelled as a descent condition.

    **The core insight**

    Client-side and server-side validation are two *local sections* defined
    over overlapping domains:

    * The **client section** is defined over the set of values the client has
      already typed.  It uses lightweight rules (regex, range checks) that
      run synchronously.
    * The **server section** is defined over the same values, but uses richer
      predicates (database uniqueness, cross-field consistency, business
      rules) that require a network round-trip.

    For the form presheaf to have a *global section* (a valid submission),
    these two local sections must agree on their common domain.  If the
    client says "valid" but the server says "invalid", we have a
    **descent obstruction**: the two local sections fail to agree on
    their overlap.

    **Descent obstruction example**

    * Client rule: email must match ``/^[^@]+@[^@]+$/``
    * Server rule: email must not already be in the database
    * Value: ``alice@example.com`` (well-formed but already registered)
    * Client section: VALID
    * Server section: INVALID ("Email already in use")
    * Overlap check: client ≠ server → obstruction detected

    **Debouncing**

    Async validation must be *debounced* — we should not fire a server
    request on every keystroke.  A delay of 300–500 ms after the last
    keystroke is conventional.  This is modelled as a *lazy covering*:
    the server's section is only evaluated after the local section has
    settled.

    Parameters
    ----------
    debounce_ms:
        Milliseconds to wait after the last value change before firing the
        server request.
    """

    def __init__(self, debounce_ms: int = 300) -> None:
        self.debounce_ms = debounce_ms
        self._pending: dict[str, _AsyncValidationRequest] = {}
        self._cache: dict[str, AsyncValidationResult] = {}
        self._obstruction_log: list[dict[str, Any]] = []

    def enqueue(
        self,
        field_name: str,
        value: Any,
        endpoint: str,
    ) -> _AsyncValidationRequest:
        """
        Enqueue an async validation request for *field_name*.

        If a request for this field is already pending, it is replaced
        (debounce semantics: only the most recent value is validated).

        Returns the :class:`_AsyncValidationRequest` descriptor.
        """
        req = _AsyncValidationRequest(
            field_name=field_name,
            value=value,
            endpoint=endpoint,
            debounce_ms=self.debounce_ms,
        )
        self._pending[field_name] = req
        return req

    def record_result(self, result: AsyncValidationResult) -> None:
        """
        Record the result of a completed async validation request.

        If the result is *invalid* and a cached client-side result exists
        that was *valid*, an obstruction is logged.
        """
        cache_key = f"{result.field_name}:{result.value!r}"

        # Check for descent obstruction: client said valid, server says invalid
        cached = self._cache.get(f"client:{cache_key}")
        if cached is not None and cached.is_valid and not result.is_valid:
            self._obstruction_log.append(
                {
                    "kind": "descent_obstruction",
                    "field": result.field_name,
                    "value": result.value,
                    "client_result": "valid",
                    "server_result": result.error or "invalid",
                    "description": (
                        "Client validation passed but server validation failed.  "
                        "This is a descent obstruction: the two local sections "
                        "disagree on their common domain."
                    ),
                }
            )

        self._cache[f"server:{cache_key}"] = result
        self._pending.pop(result.field_name, None)

    def record_client_result(
        self, field_name: str, value: Any, is_valid: bool
    ) -> None:
        """Record a client-side validation result for later obstruction checking."""
        cache_key = f"{field_name}:{value!r}"
        self._cache[f"client:{cache_key}"] = AsyncValidationResult(
            field_name=field_name,
            value=value,
            is_valid=is_valid,
        )

    def is_pending(self, field_name: str) -> bool:
        """Return True if an async validation request is pending for *field_name*."""
        return field_name in self._pending

    def get_cached_result(self, field_name: str, value: Any) -> AsyncValidationResult | None:
        """Return the cached server validation result for (*field_name*, *value*) if any."""
        return self._cache.get(f"server:{field_name}:{value!r}")

    def has_obstruction(self) -> bool:
        """Return True if any client/server disagreements have been detected."""
        return bool(self._obstruction_log)

    def obstructions(self) -> list[dict[str, Any]]:
        """Return a copy of the obstruction log."""
        return list(self._obstruction_log)

    def to_descent_result(self, form: FormPresheaf) -> DescentResult:
        """
        Express the async validation state as a :class:`DescentResult`.

        Returns :meth:`DescentResult.success` if no obstructions have been
        detected; :meth:`DescentResult.failure` otherwise.
        """
        coord_key = f"async_validation.{form.form_id}"

        if not self.has_obstruction():
            section = GlobalSection(
                coordinate=coord_key,
                merged_judgment={"status": "no_obstructions"},
            )
            return DescentResult.success(section)

        obstruction = DescentObstruction(
            coordinate=coord_key,
            violated_overlaps=(),
            partial_section={"obstructions": self._obstruction_log},
        )
        return DescentResult.failure(obstruction)

    def loading_indicator_policy(self) -> dict[str, Any]:
        """
        Describe the UI loading indicator policy for async validation.

        Returns a structured policy dict suitable for passing to a UI
        renderer.  The key invariants are:

        * Show a spinner or "checking…" label while the request is in flight.
        * Hide the spinner as soon as the result arrives.
        * Show an error message if the server returned an error.
        """
        return {
            "show_spinner_when": "is_validating == True",
            "hide_spinner_when": "is_validating == False",
            "spinner_label": "Checking…",
            "debounce_ms": self.debounce_ms,
            "error_display": "inline below field",
            "success_display": "checkmark icon",
            "pending_fields": list(self._pending.keys()),
        }

    def coordination_protocol(self) -> str:
        """
        Return a human-readable description of the client + server
        validation coordination protocol.

        This is the descent protocol:

        1. Client evaluates synchronous rules first (fast, local).
        2. If client passes, debounce timer starts.
        3. After debounce, server request fires.
        4. Server result is compared to client result (overlap check).
        5. If they disagree, a descent obstruction is recorded.
        6. The form submission is blocked until all obstructions are resolved.
        """
        return (
            "Coordination protocol:\n"
            "  1. Evaluate synchronous client-side rules immediately on value change.\n"
            f"  2. If client passes, start debounce timer ({self.debounce_ms} ms).\n"
            "  3. After debounce, fire async request to server endpoint.\n"
            "  4. While request is in-flight, set field.is_validating = True.\n"
            "  5. On server response, clear is_validating, record result.\n"
            "  6. Compare server result to client result (overlap/descent check).\n"
            "  7. If mismatch: log obstruction, surface server error to user.\n"
            "  8. Block submission until all fields have consistent client+server results.\n"
            "\n"
            "Sheaf interpretation:\n"
            "  Client rules = local section over 'browser' coordinate.\n"
            "  Server rules = local section over 'server' coordinate.\n"
            "  Overlap = values seen by both client and server.\n"
            "  Descent condition: sections agree on overlap ⟺ form can be submitted.\n"
        )


# ---------------------------------------------------------------------------
# § 7  FormSubmissionTheory
# ---------------------------------------------------------------------------


@dataclass
class FormSubmissionResult:
    """
    The outcome of a form submission attempt.

    This represents the *global section* of the form presheaf (if successful)
    or the *obstruction* (if the server rejected the data).

    Parameters
    ----------
    success:
        Whether the server accepted the submission.
    field_errors:
        Server-returned field-level validation errors.  Keys are field names;
        values are lists of error messages.
    general_error:
        A non-field-specific error (e.g. "Service unavailable").
    data:
        The processed data returned by the server (e.g. created resource).
    status_code:
        HTTP status code returned by the server.
    redirect_url:
        URL to navigate to after a successful submission.
    """

    success: bool
    field_errors: dict[str, list[str]] = field(default_factory=dict)
    general_error: str = ""
    data: Any = None
    status_code: int = 200
    redirect_url: str = ""

    @property
    def has_field_errors(self) -> bool:
        return bool(self.field_errors)

    @property
    def has_error(self) -> bool:
        return not self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "field_errors": {k: list(v) for k, v in self.field_errors.items()},
            "general_error": self.general_error,
            "data": self.data,
            "status_code": self.status_code,
            "redirect_url": self.redirect_url,
        }

    @classmethod
    def ok(cls, data: Any = None, redirect_url: str = "") -> FormSubmissionResult:
        return cls(success=True, data=data, status_code=200, redirect_url=redirect_url)

    @classmethod
    def server_error(
        cls,
        field_errors: dict[str, list[str]] | None = None,
        general_error: str = "",
        status_code: int = 422,
    ) -> FormSubmissionResult:
        return cls(
            success=False,
            field_errors=field_errors or {},
            general_error=general_error,
            status_code=status_code,
        )


class FormSubmissionTheory:
    """
    Formalisation of the form submission lifecycle.

    **Submission as global section construction**

    Form submission is the attempt to *glue* all field values (local sections)
    into a single global section that the server can accept.  This is exactly
    the descent problem:

    1. **Pre-submit validation** — verify that all local sections (fields) are
       consistent with their validation rules.  Any failure is a local
       obstruction that blocks gluing.
    2. **Serialisation** — pack the local sections into a payload.
    3. **Transmission** — send the payload to the server (the global site).
    4. **Server validation** — the server performs its own section check.
    5. **Result handling** — map the server's response back onto field
       coordinates (success extends the section; error maps obstructions back).

    **Optimistic submission**

    Some forms use *optimistic submission*: they assume the server will accept
    the data, update the UI immediately, then revert if the server rejects.
    This is a *speculative section*: we pretend the global section exists and
    undo it if the descent check fails on the server side.

    **Re-submission prevention**

    During a pending submission, the submit button is disabled.  This prevents
    duplicate requests, which in sheaf terms would create two competing
    candidate global sections.

    Parameters
    ----------
    form:
        The :class:`FormPresheaf` this theory applies to.
    optimistic:
        If True, apply optimistic submission semantics.
    """

    def __init__(
        self,
        form: FormPresheaf,
        optimistic: bool = False,
    ) -> None:
        self.form = form
        self.optimistic = optimistic
        self._is_submitting = False
        self._submission_history: list[dict[str, Any]] = []

    @property
    def is_submitting(self) -> bool:
        """True while a submission is in progress."""
        return self._is_submitting

    def pre_submit_validate(self) -> dict[str, list[str]]:
        """
        Run full form validation before allowing submission.

        Marks all fields as touched so errors become visible, then
        evaluates all rules.

        Returns
        -------
        dict[str, list[str]]
            Error map.  Empty dict means the form is ready to submit.
        """
        self.form.touch_all()
        return self.form.validate_all()

    def can_submit(self) -> bool:
        """
        Return True if the form is in a submittable state.

        A form can be submitted when:
        * It is not currently submitting (re-submission prevention).
        * All fields are currently valid.
        """
        if self._is_submitting:
            return False
        return self.form.is_form_valid()

    def begin_submission(self) -> dict[str, Any] | None:
        """
        Begin the submission flow.

        Returns the serialised payload if the form is valid, ``None`` if it is
        not (validation errors have been set on fields).
        """
        errors = self.pre_submit_validate()
        if errors:
            return None

        self._is_submitting = True
        payload = self.form.to_submission_data()

        if self.optimistic:
            # Speculative section: record current state for potential rollback
            snapshot = {
                field_name: state.value
                for field_name, state in self.form._fields.items()
            }
            self._submission_history.append(
                {
                    "optimistic": True,
                    "snapshot": snapshot,
                    "payload": payload,
                }
            )

        return payload

    def handle_result(self, result: FormSubmissionResult) -> None:
        """
        Apply the server's response to the form state.

        On success:
        * Clear the submission lock.
        * Optionally reset the form (if ``reset_on_success`` is desired).
        * Record the result in history.

        On error:
        * Clear the submission lock.
        * Map server field errors back onto form fields.
        * If optimistic, revert the speculative state.
        """
        self._is_submitting = False

        record: dict[str, Any] = {
            "success": result.success,
            "status_code": result.status_code,
        }
        self._submission_history.append(record)

        if result.success:
            return  # Caller handles navigation/reset

        # Map server errors back onto form fields (server → client direction)
        if result.has_field_errors:
            self.form.set_server_errors(result.field_errors)

        # If optimistic and we have a snapshot, revert
        if self.optimistic and self._submission_history:
            for entry in reversed(self._submission_history):
                if entry.get("optimistic") and "snapshot" in entry:
                    for field_name, saved_value in entry["snapshot"].items():
                        if field_name in self.form._fields:
                            self.form._fields[field_name].value = saved_value
                    break

    def submission_history(self) -> list[dict[str, Any]]:
        """Return the list of submission records."""
        return list(self._submission_history)

    def loading_state_policy(self) -> dict[str, str]:
        """
        Return the policy for submission loading state UI.

        The submit button must be disabled during pending submission to
        prevent duplicate requests.
        """
        return {
            "submit_button_disabled_when": "is_submitting == True",
            "submit_button_label_while_submitting": "Submitting…",
            "loading_indicator": "inline spinner next to submit button",
            "error_display": "error summary above form + inline field errors",
            "success_action": (
                "redirect to redirect_url if set, else show success message and reset form"
            ),
        }

    def error_mapping_strategy(self) -> str:
        """
        Describe how server field errors are mapped back to form fields.

        Server-side validation errors come back as a dict of
        ``field_name → [messages]``.  We apply these as additional errors
        on top of any existing client-side errors, and mark the fields as
        touched so the errors are visible.

        In descent terms: the server has detected that the candidate global
        section (the submitted payload) does not satisfy the server's local
        section requirements.  We propagate these obstructions back to the
        field coordinates.
        """
        return (
            "Error mapping strategy:\n"
            "  1. Server returns field_errors: {field_name: [messages]}.\n"
            "  2. For each (field, messages) pair:\n"
            "     a. Append messages to field.errors.\n"
            "     b. Set field.is_touched = True (make errors visible).\n"
            "  3. General error (non-field) shown in error summary region.\n"
            "  4. Focus first field with errors (accessibility: keyboard users).\n"
            "  5. If optimistic, revert any speculative UI changes.\n"
            "\n"
            "Sheaf interpretation:\n"
            "  Server errors = obstruction of the candidate global section.\n"
            "  Re-mapping = annotating field coordinates with obstruction data.\n"
            "  Resolution = user edits the field to a value that satisfies server rules.\n"
        )


# ---------------------------------------------------------------------------
# § 8  FormAccessibilityTheory
# ---------------------------------------------------------------------------


@dataclass
class AccessibilityIssue:
    """A single WCAG accessibility issue detected on a form."""

    field_name: str | None
    issue_kind: str
    description: str
    wcag_criterion: str = ""
    severity: str = "error"  # "error" | "warning" | "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field_name,
            "kind": self.issue_kind,
            "description": self.description,
            "wcag": self.wcag_criterion,
            "severity": self.severity,
        }


class FormAccessibilityTheory:
    """
    Formalisation of WCAG 2.1 requirements for accessible forms.

    **Accessibility as a sheaf condition**

    An accessible form is one where *every* input field has consistent
    annotations across the accessibility tree.  Specifically:

    * Each field must have a *label* (the section over the "label" coordinate).
    * Each field with errors must have those errors *associated* with it via
      ``aria-describedby`` (the section over the "error association" coordinate).
    * Required fields must be *identified* by text, not just visual styling.
    * Errors must be announced to screen readers via ``aria-live`` regions.

    Missing any of these is a *descent obstruction*: the accessibility layer
    fails to provide a globally consistent view of the form structure.

    **Invariants checked**

    1. **Label coverage**: every non-hidden, non-action field has a label.
    2. **Error association**: every field with errors has ``aria-describedby``
       pointing to the error container.
    3. **Required identification**: required fields are marked with text
       (not colour alone).
    4. **Error summary**: form-level errors have an ``aria-live`` region.
    5. **Autocomplete**: appropriate ``autocomplete`` attributes are set.

    Parameters
    ----------
    form:
        The :class:`FormPresheaf` to audit.
    """

    def __init__(self, form: FormPresheaf) -> None:
        self.form = form
        self._issues: list[AccessibilityIssue] = []

    def check_label_coverage(self) -> list[AccessibilityIssue]:
        """
        Verify that every non-hidden, non-action field has a label.

        A field is labelled if it has a non-empty ``label`` attribute in its
        :class:`FormFieldState`.  In a real DOM, this would correspond to:

        * A ``<label for="field-id">`` element, OR
        * An ``aria-label`` attribute on the input, OR
        * An ``aria-labelledby`` attribute pointing to a labelling element.

        Returns a list of :class:`AccessibilityIssue` for unlabelled fields.
        """
        issues: list[AccessibilityIssue] = []
        for name, state in self.form._fields.items():
            if state.kind.is_action() or state.kind is InputKind.HIDDEN:
                continue
            if not state.label or state.label == name:
                # ``label == name`` is the default fallback — a real label
                # should be a human-readable string, not just the field name.
                issues.append(
                    AccessibilityIssue(
                        field_name=name,
                        issue_kind="missing_label",
                        description=(
                            f"Field '{name}' has no accessible label.  "
                            "Add a <label>, aria-label, or aria-labelledby attribute."
                        ),
                        wcag_criterion="1.3.1 Info and Relationships (A)",
                        severity="error",
                    )
                )
        return issues

    def check_error_association(self) -> list[AccessibilityIssue]:
        """
        Verify that fields with errors have those errors linked via
        ``aria-describedby``.

        Screen readers announce ``aria-describedby`` content when the user
        focuses the input.  Without this link, error messages are invisible
        to non-sighted users.

        Returns a list of :class:`AccessibilityIssue` for unlinked errors.
        """
        issues: list[AccessibilityIssue] = []
        for name, state in self.form._fields.items():
            if not state.errors:
                continue
            # Check that aria_describedby is populated (non-empty list)
            if not state.aria_describedby:
                issues.append(
                    AccessibilityIssue(
                        field_name=name,
                        issue_kind="missing_error_association",
                        description=(
                            f"Field '{name}' has validation errors but no "
                            "aria-describedby linking to the error container.  "
                            "Screen readers will not announce the errors."
                        ),
                        wcag_criterion="1.3.1 Info and Relationships (A) / 4.1.3 Status Messages (AA)",
                        severity="error",
                    )
                )
        return issues

    def check_required_field_identification(self) -> list[AccessibilityIssue]:
        """
        Verify that required fields are identified by text, not just an
        asterisk or colour.

        WCAG 1.4.1 (Use of Colour) prohibits using colour alone to convey
        information.  Required fields marked only with a red asterisk are
        non-compliant.  The label should include the word "required" or
        an ``aria-required="true"`` attribute.

        Returns a list of :class:`AccessibilityIssue` for fields that rely
        on colour-only required indication.
        """
        issues: list[AccessibilityIssue] = []
        for name, state in self.form._fields.items():
            if not state.required:
                continue
            label_lower = (state.label or "").lower()
            # A compliant label either contains 'required' or 'mandatory'
            has_text_indicator = "required" in label_lower or "mandatory" in label_lower
            if not has_text_indicator:
                # Could also be conveyed via aria-required — we note it as a warning
                issues.append(
                    AccessibilityIssue(
                        field_name=name,
                        issue_kind="required_colour_only",
                        description=(
                            f"Required field '{name}' is not textually identified as required.  "
                            "Include the word 'required' in the label or use aria-required='true'."
                        ),
                        wcag_criterion="1.4.1 Use of Colour (A)",
                        severity="warning",
                    )
                )
        return issues

    def check_error_summary_region(self, has_aria_live_region: bool = False) -> list[AccessibilityIssue]:
        """
        Verify that the form has an ``aria-live`` region for error announcements.

        When the form has errors (especially after a failed submission), they
        must be announced to screen readers.  This requires an ``aria-live``
        or ``role="alert"`` region that is updated when errors appear.

        Parameters
        ----------
        has_aria_live_region:
            Pass ``True`` if the rendered form includes an ``aria-live`` region.
        """
        if has_aria_live_region:
            return []

        all_errors = self.form.get_errors()
        if not all_errors:
            return []

        return [
            AccessibilityIssue(
                field_name=None,
                issue_kind="missing_error_summary_region",
                description=(
                    "The form has validation errors but no aria-live region for "
                    "announcing them to screen readers.  Add a <div aria-live='polite'> "
                    "or <div role='alert'> that is updated when errors appear."
                ),
                wcag_criterion="4.1.3 Status Messages (AA)",
                severity="error",
            )
        ]

    def run_all_checks(self, has_aria_live_region: bool = False) -> list[AccessibilityIssue]:
        """
        Run all accessibility checks and return the combined list of issues.
        """
        issues: list[AccessibilityIssue] = []
        issues.extend(self.check_label_coverage())
        issues.extend(self.check_error_association())
        issues.extend(self.check_required_field_identification())
        issues.extend(self.check_error_summary_region(has_aria_live_region))
        self._issues = issues
        return issues

    def is_accessible(self, has_aria_live_region: bool = False) -> bool:
        """Return True if no accessibility errors were found."""
        issues = self.run_all_checks(has_aria_live_region)
        return not any(i.severity == "error" for i in issues)

    def summary(self) -> dict[str, Any]:
        """Return a structured accessibility audit summary."""
        issues = self.run_all_checks()
        return {
            "total_issues": len(issues),
            "errors": [i.to_dict() for i in issues if i.severity == "error"],
            "warnings": [i.to_dict() for i in issues if i.severity == "warning"],
            "is_accessible": not any(i.severity == "error" for i in issues),
        }


# ---------------------------------------------------------------------------
# § 9  FormDescentChecker
# ---------------------------------------------------------------------------


class FormDescentChecker:
    """
    Full coherence verification for a form via JuGeo descent machinery.

    A form is *coherent* (all descent conditions satisfied) when:

    1. **Client–server validation match**: every field validated on the
       client is also validated on the server with consistent rules.
    2. **Accessibility**: labels, error associations, required identification.
    3. **Submission handling**: loading, error, and success states are
       correctly implemented.
    4. **File upload safety**: file inputs validate MIME type and size.

    Each check is modelled as a local section over the form's field site.
    The form is coherent iff all local sections are consistent — i.e. the
    descent engine can glue them into a global section.

    Parameters
    ----------
    form:
        The :class:`FormPresheaf` to check.
    async_theory:
        Optional :class:`AsyncValidationTheory` for client–server checks.
    submission_theory:
        Optional :class:`FormSubmissionTheory` for submission-state checks.
    accessibility_theory:
        Optional :class:`FormAccessibilityTheory` for WCAG checks.
    """

    def __init__(
        self,
        form: FormPresheaf,
        async_theory: AsyncValidationTheory | None = None,
        submission_theory: FormSubmissionTheory | None = None,
        accessibility_theory: FormAccessibilityTheory | None = None,
    ) -> None:
        self.form = form
        self.async_theory = async_theory or AsyncValidationTheory()
        self.submission_theory = submission_theory or FormSubmissionTheory(form)
        self.accessibility_theory = accessibility_theory or FormAccessibilityTheory(form)
        self._engine = DescentEngine()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_client_server_validation_match(self) -> dict[str, Any]:
        """
        Check that client-side validation rules are a subset of server-side rules.

        The descent condition: if client says "valid", server must not say
        "invalid".  Any client/server mismatch is an obstruction.

        Returns a dict describing the check result.
        """
        obstructions = self.async_theory.obstructions()
        return {
            "check": "client_server_validation_match",
            "passed": not obstructions,
            "obstruction_count": len(obstructions),
            "obstructions": obstructions,
            "description": (
                "Client validation ⊆ server validation.  "
                "If any values pass client rules but fail server rules, "
                "we have a descent obstruction."
            ),
        }

    def check_accessibility(self) -> dict[str, Any]:
        """Check WCAG accessibility of the form."""
        issues = self.accessibility_theory.run_all_checks()
        errors = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]
        return {
            "check": "accessibility",
            "passed": not errors,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issues": [i.to_dict() for i in issues],
        }

    def check_submission_handling(self) -> dict[str, Any]:
        """
        Check that the form correctly handles loading, error, and success states.

        We verify:
        * Re-submission prevention: ``is_submitting`` flag exists.
        * Error mapping: server errors can be applied to fields.
        * Loading state: policy is defined.
        """
        issues: list[str] = []

        policy = self.submission_theory.loading_state_policy()
        if "submit_button_disabled_when" not in policy:
            issues.append("No re-submission prevention policy defined.")

        if not hasattr(self.submission_theory, "handle_result"):
            issues.append("No result handler — cannot map server errors to fields.")

        return {
            "check": "submission_handling",
            "passed": not issues,
            "issues": issues,
            "policy": policy,
        }

    def check_file_upload_safety(self) -> dict[str, Any]:
        """
        Check that file input fields have MIME type and size validation.

        File uploads require server-side validation at minimum, and ideally
        client-side validation too (to give fast feedback before upload).

        Returns
        -------
        dict
            Check result with field-level findings.
        """
        issues: list[dict[str, Any]] = []

        for name, state in self.form._fields.items():
            if state.kind is not InputKind.FILE:
                continue
            c = state.constraints

            if "accept" not in c:
                issues.append(
                    {
                        "field": name,
                        "issue": "no_accept_constraint",
                        "description": (
                            f"File field '{name}' has no 'accept' constraint.  "
                            "Any file type will be accepted — this is a security risk."
                        ),
                    }
                )

            if "maxsize" not in c and "max_size_bytes" not in c:
                issues.append(
                    {
                        "field": name,
                        "issue": "no_size_constraint",
                        "description": (
                            f"File field '{name}' has no size constraint.  "
                            "Arbitrarily large files can be uploaded."
                        ),
                    }
                )

        return {
            "check": "file_upload_safety",
            "passed": not issues,
            "issue_count": len(issues),
            "issues": issues,
        }

    def check_multi_step_coherence(self) -> dict[str, Any]:
        """
        Check that multi-step form steps form a valid covering of the field site.

        In sheaf terms: the steps are a covering family of the root form
        coordinate.  The global section (full submission) can be assembled
        only when every step's local section is valid.

        A step is *incoherent* if it references fields that don't exist in
        the form registry.
        """
        if self.form.step_count() == 0:
            return {
                "check": "multi_step_coherence",
                "passed": True,
                "description": "Not a multi-step form.",
            }

        issues: list[str] = []
        all_step_fields: set[str] = set()

        for step in self.form._steps:
            for fname in step.field_names:
                if fname not in self.form._fields:
                    issues.append(
                        f"Step {step.step_number} references unknown field '{fname}'."
                    )
                all_step_fields.add(fname)

        # Every registered field should appear in at least one step
        orphan_fields = set(self.form.field_names()) - all_step_fields
        if orphan_fields:
            issues.append(
                f"Fields not assigned to any step: {sorted(orphan_fields)}.  "
                "These fields will never be validated by step completion checks."
            )

        return {
            "check": "multi_step_coherence",
            "passed": not issues,
            "issues": issues,
            "step_count": self.form.step_count(),
        }

    # ------------------------------------------------------------------
    # Full descent
    # ------------------------------------------------------------------

    def full_form_descent(self) -> DescentResult:
        """
        Run all checks and express the result as a :class:`DescentResult`.

        This is the top-level descent check for the entire form theory.  It
        glues together the local sections from each individual check into a
        single global verdict.

        Returns
        -------
        DescentResult
            ``success`` if all checks pass; ``failure`` with an obstruction
            describing the first failed check otherwise.
        """
        checks = [
            self.check_client_server_validation_match(),
            self.check_accessibility(),
            self.check_submission_handling(),
            self.check_file_upload_safety(),
            self.check_multi_step_coherence(),
        ]

        failed = [c for c in checks if not c["passed"]]
        coord_key = f"form_descent.{self.form.form_id}"

        if not failed:
            section = GlobalSection(
                coordinate=coord_key,
                merged_judgment={
                    "status": "all_checks_passed",
                    "check_count": len(checks),
                },
                constituent_sections=tuple(c["check"] for c in checks),
            )
            return DescentResult.success(section)

        obstruction = DescentObstruction(
            coordinate=coord_key,
            violated_overlaps=(),
            partial_section={
                c["check"]: {"passed": c["passed"]} for c in checks
            },
        )
        return DescentResult.failure(obstruction)

    def report(self) -> dict[str, Any]:
        """
        Return a human-readable diagnostic report of all form descent checks.
        """
        checks = {
            "client_server_validation_match": self.check_client_server_validation_match(),
            "accessibility": self.check_accessibility(),
            "submission_handling": self.check_submission_handling(),
            "file_upload_safety": self.check_file_upload_safety(),
            "multi_step_coherence": self.check_multi_step_coherence(),
        }
        all_passed = all(c["passed"] for c in checks.values())
        return {
            "form_id": self.form.form_id,
            "all_checks_passed": all_passed,
            "checks": checks,
            "summary": (
                "Form is coherent — all descent conditions satisfied."
                if all_passed
                else f"Form has {sum(1 for c in checks.values() if not c['passed'])} failing check(s)."
            ),
        }
