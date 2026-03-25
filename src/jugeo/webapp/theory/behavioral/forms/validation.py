from __future__ import annotations

"""Form validation modelled as descent conditions.

Client-side validation is a local section check over the user's input.
Server-side validation is the global check that determines whether a
submitted form can be "glued" into a valid server-side state.  Any
mismatch between the two is a descent obstruction in the sense of
``jugeo.geometry.descent``.
"""

__all__ = [
    "InputType",
    "ValidationRule",
    "FormField",
    "FormSchema",
    "ServerValidationResult",
    "ValidationCoherenceChecker",
    "MultiStepFormState",
]

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.descent import (
    DescentObstruction,
    DescentResult,
    GlobalSection,
    LocalSection,
    OverlapCondition,
    OverlapStatus,
    RepairFrontier,
)


# ---------------------------------------------------------------------------
# 1.  InputType
# ---------------------------------------------------------------------------

class InputType(str, Enum):
    """HTML input types that may appear in a form field."""

    TEXT = "text"
    EMAIL = "email"
    PASSWORD = "password"
    NUMBER = "number"
    INTEGER = "integer"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    URL = "url"
    TEL = "tel"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    FILE = "file"
    TEXTAREA = "textarea"
    HIDDEN = "hidden"
    COLOR = "color"
    RANGE = "range"

    # Numeric input types whose "required" check only needs a non-None value.
    @property
    def is_numeric(self) -> bool:
        return self in {InputType.NUMBER, InputType.INTEGER, InputType.RANGE}


# ---------------------------------------------------------------------------
# 2.  ValidationRule
# ---------------------------------------------------------------------------

@dataclass
class ValidationRule:
    """A single validation constraint attached to a form field.

    ``rule_kind`` is one of: required, minlength, maxlength, min, max,
    pattern, email, url, custom.
    ``value`` is the constraint parameter (e.g. 8 for ``minlength=8``).
    ``error_message`` overrides the default message when non-empty.
    """

    rule_kind: str
    value: Any = None
    error_message: str = ""

    # -- default messages per rule kind -------------------------------------

    _DEFAULT_MESSAGES: dict[str, str] = field(
        default_factory=lambda: {}, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self._DEFAULT_MESSAGES = {
            "required": "This field is required.",
            "minlength": f"Must be at least {self.value} characters.",
            "maxlength": f"Must be at most {self.value} characters.",
            "min": f"Must be at least {self.value}.",
            "max": f"Must be at most {self.value}.",
            "pattern": "Value does not match the required pattern.",
            "email": "Enter a valid email address.",
            "url": "Enter a valid URL (must start with http:// or https://).",
            "custom": "Invalid value.",
        }

    def message(self) -> str:
        """Return the effective error message for this rule."""
        if self.error_message:
            return self.error_message
        return self._DEFAULT_MESSAGES.get(self.rule_kind, "Invalid value.")

    # -- factory ------------------------------------------------------------

    @classmethod
    def standard_rules(cls) -> dict[str, ValidationRule]:
        """Return a dict of commonly-used pre-built rules."""
        return {
            "required": cls("required", error_message="This field is required."),
            "email": cls("email", error_message="Enter a valid email address."),
            "url": cls(
                "url",
                error_message="Enter a valid URL (must start with http:// or https://).",
            ),
            "min_length_8": cls("minlength", value=8, error_message="Must be at least 8 characters."),
            "max_length_255": cls("maxlength", value=255, error_message="Must be at most 255 characters."),
            "min_zero": cls("min", value=0, error_message="Must be zero or greater."),
            "max_100": cls("max", value=100, error_message="Must be 100 or less."),
            "no_whitespace": cls(
                "pattern",
                value=r"\S+",
                error_message="Must not contain whitespace.",
            ),
        }


# ---------------------------------------------------------------------------
# 3.  FormField
# ---------------------------------------------------------------------------

@dataclass
class FormField:
    """A single field within a form schema, carrying its validation rules.

    ``validate_value`` applies each rule in order and returns a list of
    error messages; an empty list means the value is valid.
    """

    field_name: str
    input_type: InputType
    rules: list[ValidationRule]
    label: str = ""
    placeholder: str = ""

    # -- validation ---------------------------------------------------------

    def validate_value(self, value: Any) -> list[str]:  # noqa: C901  (complexity fine here)
        """Apply all rules to *value* and return error messages.

        Returns an empty list when the value passes every rule.
        """
        errors: list[str] = []

        for rule in self.rules:
            kind = rule.rule_kind

            if kind == "required":
                # Numeric fields: None is missing, but 0 is a valid value.
                if self.input_type.is_numeric:
                    valid = value is not None
                else:
                    valid = value is not None and value != "" and bool(value) or (
                        isinstance(value, (list, tuple)) and len(value) > 0
                        if isinstance(value, (list, tuple))
                        else value is not None and value != ""
                    )
                    # Simplify: for text-like fields 0 is not expected, so we
                    # use the stricter "not None and not empty-string" test.
                    valid = value is not None and value != ""
                if not valid:
                    errors.append(rule.message())

            elif kind == "minlength":
                if value is not None and value != "":
                    if len(str(value)) < int(rule.value):
                        errors.append(rule.message())

            elif kind == "maxlength":
                if value is not None and value != "":
                    if len(str(value)) > int(rule.value):
                        errors.append(rule.message())

            elif kind == "min":
                if value is not None and value != "":
                    try:
                        if float(value) < float(rule.value):
                            errors.append(rule.message())
                    except (TypeError, ValueError):
                        errors.append(rule.message())

            elif kind == "max":
                if value is not None and value != "":
                    try:
                        if float(value) > float(rule.value):
                            errors.append(rule.message())
                    except (TypeError, ValueError):
                        errors.append(rule.message())

            elif kind == "pattern":
                if value is not None and value != "":
                    if not re.fullmatch(rule.value, str(value)):
                        errors.append(rule.message())

            elif kind == "email":
                if value is not None and value != "":
                    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", str(value)):
                        errors.append(rule.message())

            elif kind == "url":
                if value is not None and value != "":
                    s = str(value)
                    if not (s.startswith("http://") or s.startswith("https://")):
                        errors.append(rule.message())

            # "custom" rules carry no default logic; they must be pre-applied
            # externally (e.g. by a view layer that replaces error_message).

        return errors

    def is_required(self) -> bool:
        """Return True if any attached rule is a 'required' rule."""
        return any(r.rule_kind == "required" for r in self.rules)


# ---------------------------------------------------------------------------
# 4.  FormSchema
# ---------------------------------------------------------------------------

@dataclass
class FormSchema:
    """The static description of a form: its fields, HTTP method, and action.

    ``validate`` runs client-side validation and returns a mapping from
    field name to a (possibly empty) list of error messages.
    ``to_local_section`` packages the validated data as a ``LocalSection``
    that can participate in a descent computation.
    """

    form_id: str
    fields: list[FormField]
    method: str = "POST"
    action: str = ""

    # -- field lookup -------------------------------------------------------

    def field(self, name: str) -> FormField | None:
        """Return the ``FormField`` with the given name, or None."""
        for f in self.fields:
            if f.field_name == name:
                return f
        return None

    def required_fields(self) -> list[str]:
        """Return the names of all required fields."""
        return [f.field_name for f in self.fields if f.is_required()]

    # -- validation ---------------------------------------------------------

    def validate(self, data: dict[str, Any]) -> dict[str, list[str]]:
        """Validate *data* against the schema.

        Returns ``{field_name: [error, ...]}``; fields with no errors are
        omitted from the result.  Missing required fields get a "required"
        error even when absent from *data*.
        """
        result: dict[str, list[str]] = {}

        for form_field in self.fields:
            value = data.get(form_field.field_name)
            errors = form_field.validate_value(value)
            if errors:
                result[form_field.field_name] = errors

        return result

    def is_valid(self, data: dict[str, Any]) -> bool:
        """Return True when *data* passes all field validations."""
        return not self.validate(data)

    # -- descent integration ------------------------------------------------

    def to_local_section(self, form_data: dict[str, Any]) -> LocalSection:
        """Package *form_data* as a ``LocalSection`` for descent.

        The section's coordinate is ``form/<form_id>``.  Its
        ``judgment_data`` records whether client-side validation passed
        and which fields (if any) had errors.  The trust level is 1.0
        when fully valid, 0.5 when partially valid, and 0.0 on complete
        failure.
        """
        errors = self.validate(form_data)
        valid = not errors
        error_count = sum(len(v) for v in errors.values())
        field_count = len(self.fields)

        if valid:
            trust = 1.0
        elif field_count > 0:
            passing = field_count - len(errors)
            trust = max(0.0, passing / field_count)
        else:
            trust = 0.0

        obligations = (
            [f"fix_field:{name}" for name in errors] if errors else []
        )

        return LocalSection(
            coordinate=f"form/{self.form_id}",
            judgment_data={
                "form_id": self.form_id,
                "client_valid": valid,
                "field_errors": errors,
                "submitted_data": form_data,
                "error_count": error_count,
            },
            evidence_bundle=(
                ("client_validation_passed",) if valid
                else tuple(f"client_error:{n}" for n in errors)
            ),
            trust_level=trust,
            provenance=("client_side_validation",),
            is_partial=not valid,
            residual_obligations=obligations,
        )


# ---------------------------------------------------------------------------
# 5.  ServerValidationResult
# ---------------------------------------------------------------------------

@dataclass
class ServerValidationResult:
    """The outcome of server-side validation for a form submission.

    ``field_errors`` mirrors the shape of ``FormSchema.validate``'s output.
    ``non_field_errors`` captures form-level errors (e.g. duplicate entry).
    ``success`` is True only when the submission was accepted.
    """

    field_errors: dict[str, list[str]]
    non_field_errors: list[str]
    success: bool


# ---------------------------------------------------------------------------
# 6.  ValidationCoherenceChecker
# ---------------------------------------------------------------------------

class ValidationCoherenceChecker:
    """Check whether client and server validation agree.

    A mismatch is a *descent obstruction*: the local section (client) and
    the global check (server) are incompatible, so no global section can be
    formed.  We distinguish two directions:

    * **Server-stricter**: client passed a field, server rejected it.  This
      is the normal case when server has additional business logic.
    * **Client-stricter bug**: client rejected a field, server accepted it.
      This indicates a bug in the client validation rules.
    """

    def check_coherence(
        self,
        client_schema: FormSchema,
        server_result: ServerValidationResult,
        submitted_data: dict[str, Any],
    ) -> DescentResult:
        """Return a ``DescentResult`` encoding whether validation is coherent.

        If the server accepted the form (``server_result.success``), descent
        succeeds and we return a ``GlobalSection``.  Otherwise we analyse the
        mismatch between client and server errors and return a
        ``DescentObstruction`` annotated with the discrepant fields.
        """
        client_errors = client_schema.validate(submitted_data)

        # Fields the client considered valid.
        client_passed: set[str] = {
            f.field_name for f in client_schema.fields
            if f.field_name not in client_errors
        }
        # Fields the client flagged.
        client_failed: set[str] = set(client_errors)

        server_failed: set[str] = set(server_result.field_errors)

        # Server-stricter: server rejected fields the client passed.
        server_only_failures = server_failed - client_failed

        # Client-stricter bug: client rejected fields the server accepted.
        client_only_failures = client_failed - server_failed

        if server_result.success:
            # Both sides agree: form is valid.
            section = GlobalSection(
                coordinate=f"form/{client_schema.form_id}",
                merged_judgment={
                    "form_id": client_schema.form_id,
                    "valid": True,
                    "submitted_data": submitted_data,
                    "client_errors": client_errors,
                    "server_errors": server_result.field_errors,
                    "non_field_errors": server_result.non_field_errors,
                },
                constituent_sections=(
                    f"client/form/{client_schema.form_id}",
                    f"server/form/{client_schema.form_id}",
                ),
                trust_floor=1.0,
            )
            return DescentResult.success(section)

        # Build overlap conditions for each discrepant field.
        violated: list[OverlapCondition] = []

        for field_name in server_only_failures:
            server_msgs = server_result.field_errors[field_name]
            violated.append(
                OverlapCondition(
                    left_coordinate=f"client/field/{field_name}",
                    right_coordinate=f"server/field/{field_name}",
                    overlap_coordinate=f"overlap/field/{field_name}",
                    compatibility_predicate=lambda a, b, _msgs=server_msgs: False,
                    status=OverlapStatus.VIOLATED,
                )
            )

        for field_name in client_only_failures:
            client_msgs = client_errors[field_name]
            violated.append(
                OverlapCondition(
                    left_coordinate=f"client/field/{field_name}",
                    right_coordinate=f"server/field/{field_name}",
                    overlap_coordinate=f"overlap/field/{field_name}",
                    compatibility_predicate=lambda a, b, _msgs=client_msgs: False,
                    status=OverlapStatus.VIOLATED,
                )
            )

        # Also capture non-field server errors as a form-level overlap.
        if server_result.non_field_errors:
            violated.append(
                OverlapCondition(
                    left_coordinate=f"client/form/{client_schema.form_id}",
                    right_coordinate=f"server/form/{client_schema.form_id}",
                    overlap_coordinate=f"overlap/form/{client_schema.form_id}",
                    compatibility_predicate=lambda a, b: False,
                    status=OverlapStatus.VIOLATED,
                )
            )

        partial: dict[str, Any] = {
            "agreed_passed": sorted(
                client_passed - server_failed - client_only_failures
            ),
            "agreed_failed": sorted(client_failed & server_failed),
        }

        obstruction = DescentObstruction(
            coordinate=f"form/{client_schema.form_id}",
            violated_overlaps=tuple(violated),
            partial_section=partial if partial["agreed_passed"] else None,
        )

        return DescentResult.failure(obstruction)


# ---------------------------------------------------------------------------
# 7.  MultiStepFormState
# ---------------------------------------------------------------------------

@dataclass
class MultiStepFormState:
    """Persistent state for a wizard / multi-step form.

    Steps are 0-indexed.  ``step_data`` holds submitted values per step.
    ``completed_steps`` tracks which steps have been successfully submitted.
    """

    form_id: str
    total_steps: int
    current_step: int = 0
    step_data: dict[int, dict[str, Any]] = field(default_factory=dict)
    completed_steps: set[int] = field(default_factory=set)

    # -- navigation ---------------------------------------------------------

    def advance(self) -> bool:
        """Move to the next step.  Returns True if the step was incremented."""
        if self.current_step < self.total_steps - 1:
            self.current_step += 1
            return True
        return False

    def go_back(self) -> bool:
        """Move to the previous step.  Returns True if the step was decremented."""
        if self.current_step > 0:
            self.current_step -= 1
            return True
        return False

    # -- data access --------------------------------------------------------

    def all_data(self) -> dict[str, Any]:
        """Merge data from all steps into a single flat dict.

        Later steps override earlier steps on key collisions.
        """
        merged: dict[str, Any] = {}
        for step_index in sorted(self.step_data):
            merged.update(self.step_data[step_index])
        return merged

    def is_complete(self) -> bool:
        """Return True when every step has been completed."""
        return all(step in self.completed_steps for step in range(self.total_steps))

    # -- helpers ------------------------------------------------------------

    def record_step(self, step_index: int, data: dict[str, Any]) -> None:
        """Store *data* for *step_index* and mark the step as completed."""
        self.step_data[step_index] = data
        self.completed_steps.add(step_index)

    def current_step_data(self) -> dict[str, Any]:
        """Return the data recorded for the current step, or an empty dict."""
        return self.step_data.get(self.current_step, {})
