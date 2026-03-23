r"""theory2.tex Ch31 — Core Algorithms for Partiality Encoding and Model Reconstruction.

This module implements the core algorithms described in Ch31 of theory2.tex,
including:

- Partial function encoding as Z3 relations with domain predicates
- Z3 model decoding into algebraic surface values
- Evidence reconstruction from satisfying models
- Branch sensitivity analysis
- Totalization of partial SMT2 expressions
- Merging of reconstructed models
- Model faithfulness validation

.. math::

   \\text{encode}(f : A \\rightharpoonup B)
   = (\\mathrm{dom}_f : A \\to \\mathbb{B},\\;
      R_f : A \\times B \\to \\mathbb{B})

.. math::

   \\text{reconstruct}(M, Q) = \\{v \\mapsto M(v) \\mid v \\in \\mathrm{vars}(Q)\\}
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard library imports
# ---------------------------------------------------------------------------
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
import dataclasses
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Optional jugeo subpackage imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.solver.z3_session import Z3Session, Z3Formula, Z3Encoder, Z3Decoder, Z3Result
    _Z3_SESSION_AVAILABLE = True
except ImportError:
    _Z3_SESSION_AVAILABLE = False
    class Z3Session: pass  # type: ignore[misc]
    class Z3Formula: pass  # type: ignore[misc]
    class Z3Encoder: pass  # type: ignore[misc]
    class Z3Decoder: pass  # type: ignore[misc]
    class Z3Result: pass  # type: ignore[misc]

try:
    from jugeo.solver.reconstruction import ModelReconstructor as SolverModelReconstruction
    _RECONSTRUCTION_AVAILABLE = True
except ImportError:
    _RECONSTRUCTION_AVAILABLE = False
    class SolverModelReconstruction: pass  # type: ignore[misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, Judgment
    _JUDGMENTS_AVAILABLE = True
except ImportError:
    _JUDGMENTS_AVAILABLE = False
    class JudgmentTerm: pass  # type: ignore[misc]
    class Judgment: pass  # type: ignore[misc]

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False
    class TrustAlgebra: pass  # type: ignore[misc]
    class TrustLevel: pass  # type: ignore[misc]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AlgorithmStatus(str, Enum):
    """Lifecycle status of a Ch31 algorithm execution.

    Each algorithm run transitions through these states:
    NOT_STARTED -> RUNNING -> SUCCESS | FAILED | PARTIAL_SUCCESS.
    PARTIAL_SUCCESS indicates that the algorithm produced usable output
    but encountered non-fatal warnings during execution.
    """

    NOT_STARTED = "not_started"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL_SUCCESS = "partial_success"


class MergeStrategy(str, Enum):
    """Strategy for merging multiple reconstructed Z3 models.

    Strategies correspond to different semantic interpretations of how
    partial information from multiple satisfying models should be combined:

    - LEFT_BIASED  : first model wins on conflicts
    - RIGHT_BIASED : last model wins on conflicts
    - UNION        : keep all values (as lists) on conflicts
    - INTERSECTION : keep only keys with consistent values across all models
    - CONFLICT_FAIL: treat any conflict as a hard error
    """

    LEFT_BIASED = "left_biased"
    RIGHT_BIASED = "right_biased"
    UNION = "union"
    INTERSECTION = "intersection"
    CONFLICT_FAIL = "conflict_fail"


class ValidationLevel(str, Enum):
    """Depth of model faithfulness validation.

    - SYNTAX    : check structural well-formedness only
    - SEMANTICS : check that assignments satisfy declared constraints
    - TRUST     : additionally verify trust annotations are consistent
    - FULL      : all of the above plus provenance checking
    """

    SYNTAX = "syntax"
    SEMANTICS = "semantics"
    TRUST = "trust"
    FULL = "full"


# ---------------------------------------------------------------------------
# AlgorithmResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class AlgorithmResult:
    """Container for the outcome of a single algorithm execution.

    Bundles status, the actual result payload, diagnostic messages, timing
    information, and the algorithm name into one uniform structure.

    Attributes
    ----------
    status:
        Current lifecycle status of the algorithm run.
    result:
        Arbitrary result payload produced by the algorithm.  The type
        depends on the specific algorithm; callers must inspect *status*
        before trusting this value.
    errors:
        List of error message strings accumulated during execution.
    warnings:
        List of non-fatal warning strings accumulated during execution.
    execution_time:
        Wall-clock seconds consumed by the algorithm invocation.
    algorithm_name:
        Human-readable name of the algorithm that produced this result.
    """

    status: AlgorithmStatus
    result: Any
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    execution_time: float = 0.0
    algorithm_name: str = ""

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_success(self) -> bool:
        """Return True iff the algorithm completed successfully (possibly with warnings)."""
        return self.status in (AlgorithmStatus.SUCCESS, AlgorithmStatus.PARTIAL_SUCCESS)

    def is_failure(self) -> bool:
        """Return True iff the algorithm terminated with a hard failure."""
        return self.status == AlgorithmStatus.FAILED

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this result to a plain dictionary.

        The *status* field is converted to its string value, and *result*
        is serialised as-is (caller is responsible for JSON-serialisability
        if downstream use requires it).

        Returns
        -------
        dict[str, Any]
            A plain dictionary representation of this result.
        """
        # Attempt to serialise result to a JSON-safe form
        try:
            serialised_result = json.loads(json.dumps(self.result, default=str))
        except (TypeError, ValueError):
            serialised_result = str(self.result)

        return {
            "status": self.status.value,
            "result": serialised_result,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "execution_time": self.execution_time,
            "algorithm_name": self.algorithm_name,
            "is_success": self.is_success(),
            "is_failure": self.is_failure(),
        }

    def __repr__(self) -> str:
        return (
            f"AlgorithmResult(status={self.status.value!r}, "
            f"algorithm={self.algorithm_name!r}, "
            f"errors={len(self.errors)}, "
            f"execution_time={self.execution_time:.4f}s)"
        )


# ---------------------------------------------------------------------------
# Core algorithm functions
# ---------------------------------------------------------------------------


def encode_partial_function(
    name: str,
    domain_sort: str,
    range_sort: str,
    guard_expr: str,
    body_expr: str,
) -> dict[str, Any]:
    """Encode a partial function as a Z3 relation with a domain predicate.

    Implements the encoding from Ch31 §31.1:

    .. math::

       \\text{encode}(f : A \\rightharpoonup B)
       = (\\mathrm{dom}_f : A \\to \\mathbb{B},\\;
          R_f : A \\times B \\to \\mathbb{B})

    The encoding produces two SMTLIB2 declarations:

    1. A *domain predicate* ``{name}_dom`` of sort ``A -> Bool`` whose
       models restrict to the subdomain on which the function is defined.
    2. A *relation* ``{name}_rel`` of sort ``A -> B`` that agrees with the
       partial function wherever the domain predicate holds.

    Parameters
    ----------
    name:
        The name of the partial function being encoded.  Must be a
        non-empty string that forms a valid SMT2 identifier.
    domain_sort:
        The SMT2 sort name for the domain type (e.g. ``"Int"``, ``"A"``).
    range_sort:
        The SMT2 sort name for the range type (e.g. ``"Bool"``, ``"B"``).
    guard_expr:
        An SMT2 boolean expression in variable ``x`` that characterises the
        domain predicate (the *guard*).  Example: ``"(>= x 0)"``.
    body_expr:
        An SMT2 expression in variable ``x`` that gives the function value.
        Example: ``"(* x x)"``.

    Returns
    -------
    dict[str, Any]
        A dictionary containing:

        - ``smt2``        : the full SMTLIB2 encoding string
        - ``domain_pred`` : the name of the domain predicate declaration
        - ``relation``    : the name of the relation declaration
        - ``encoding_id`` : a unique UUID for this encoding instance
        - ``metadata``    : dict of provenance information

    Raises
    ------
    ValueError
        If any of the string arguments is empty.
    """
    # Validate all inputs are non-empty strings
    for arg_name, arg_val in [
        ("name", name),
        ("domain_sort", domain_sort),
        ("range_sort", range_sort),
        ("guard_expr", guard_expr),
        ("body_expr", body_expr),
    ]:
        if not isinstance(arg_val, str) or not arg_val.strip():
            raise ValueError(
                f"encode_partial_function: argument '{arg_name}' must be a non-empty string, "
                f"got {arg_val!r}"
            )

    # Generate a stable encoding ID from the inputs for reproducibility,
    # then append a UUID suffix to ensure uniqueness across invocations.
    content_hash = hashlib.sha256(
        f"{name}|{domain_sort}|{range_sort}|{guard_expr}|{body_expr}".encode()
    ).hexdigest()[:8]
    encoding_id = f"{content_hash}-{uuid.uuid4()}"

    # Derive the two declaration names from the function name
    domain_pred_name = f"{name}_dom"
    relation_name = f"{name}_rel"

    # Build the SMTLIB2 encoding string line by line
    smt2_lines: list[str] = [
        f"; =========================================================",
        f"; Partial function encoding: {name} : {domain_sort} -> {range_sort}",
        f"; Generated by encode_partial_function (encoding_id={encoding_id})",
        f"; Ch31 §31.1 — domain predicate + relation",
        f"; =========================================================",
        "",
        f"; --- Relation declaration ---",
        f"(declare-fun {relation_name} ({domain_sort}) {range_sort})",
        "",
        f"; --- Domain predicate declaration ---",
        f"(declare-fun {domain_pred_name} ({domain_sort}) Bool)",
        "",
        f"; --- Guard axiom ---",
        f"; The domain predicate implies the guard expression holds.",
        f"(assert (forall ((x {domain_sort}))",
        f"  (=> ({domain_pred_name} x) {guard_expr})))",
        "",
        f"; --- Body definition ---",
        f"; On the domain, the relation equals the body expression.",
        f"(assert (forall ((x {domain_sort}))",
        f"  (=> ({domain_pred_name} x)",
        f"      (= ({relation_name} x) {body_expr}))))",
        "",
        f"; --- End encoding for {name} ---",
    ]
    smt2 = "\n".join(smt2_lines)

    # Assemble and return the encoding dictionary
    return {
        "smt2": smt2,
        "domain_pred": domain_pred_name,
        "relation": relation_name,
        "encoding_id": encoding_id,
        "content_hash": content_hash,
        "metadata": {
            "name": name,
            "domain_sort": domain_sort,
            "range_sort": range_sort,
            "guard_expr": guard_expr,
            "body_expr": body_expr,
            "created_at": time.time(),
            "smt2_lines": len(smt2_lines),
        },
    }


# ---------------------------------------------------------------------------


def decode_z3_model_to_surface(
    model_dict: dict[str, Any],
    sort_name: str,
    constructors: list[str],
) -> dict[str, Any]:
    """Decode a raw Z3 model dictionary into an algebraic surface representation.

    Implements the decoding step from Ch31 §31.3:  given a satisfying
    assignment ``M`` from Z3 and the constructors of an algebraic surface,
    partition the model entries by constructor.

    The heuristic for matching is:
    - a model key ``k`` is associated with constructor ``c`` if ``k`` starts
      with ``c`` or contains ``c`` as a substring (case-sensitive).

    Parameters
    ----------
    model_dict:
        Raw key-value assignments from a Z3 model.
    sort_name:
        The name of the algebraic sort being reconstructed.
    constructors:
        List of constructor names to look for in the model keys.

    Returns
    -------
    dict[str, Any]
        - ``sort_name``      : the sort name passed in
        - ``reconstructed``  : dict mapping each constructor to its matched assignments
        - ``total_matched``  : total number of matched key-value pairs
        - ``unmatched_keys`` : list of keys that did not match any constructor
    """
    if not model_dict:
        return {
            "sort_name": sort_name,
            "reconstructed": {c: {} for c in constructors},
            "total_matched": 0,
            "unmatched_keys": [],
        }

    # Build a mapping from constructor name -> dict of matched assignments
    reconstructed: dict[str, dict[str, Any]] = {c: {} for c in constructors}
    matched_keys: set[str] = set()

    for key, value in model_dict.items():
        # Try to match the key to a constructor by prefix or substring
        matched = False
        for constructor in constructors:
            if key.startswith(constructor) or constructor in key:
                reconstructed[constructor][key] = value
                matched_keys.add(key)
                matched = True
                break  # assign to the first matching constructor only

        # Also check by lowercased comparison for robustness
        if not matched:
            key_lower = key.lower()
            for constructor in constructors:
                if constructor.lower() in key_lower:
                    reconstructed[constructor][key] = value
                    matched_keys.add(key)
                    break

    # Collect keys that did not match any constructor
    unmatched_keys = [k for k in model_dict.keys() if k not in matched_keys]

    # Count total matched pairs
    total_matched = sum(len(v) for v in reconstructed.values())

    return {
        "sort_name": sort_name,
        "reconstructed": reconstructed,
        "total_matched": total_matched,
        "unmatched_keys": unmatched_keys,
        "constructors_used": [c for c in constructors if reconstructed[c]],
        "constructors_empty": [c for c in constructors if not reconstructed[c]],
        "decoded_at": time.time(),
    }


# ---------------------------------------------------------------------------


def reconstruct_evidence_from_model(
    model_dict: dict[str, Any],
    query_id: str,
    trust_level: str,
) -> dict[str, Any]:
    """Reconstruct evidence from a Z3 satisfying model.

    Implements Ch31 §31.4: given a satisfying assignment from Z3, build an
    evidence record that can be consumed by the JuGeo evidence infrastructure.

    The reconstruction groups assignments by their Python type, builds a
    provenance list, and packages everything with metadata.

    Parameters
    ----------
    model_dict:
        Key-value assignments from a Z3 satisfying model.
    query_id:
        Identifier of the Z3 query that produced this model.
    trust_level:
        Trust level string to attach to the reconstructed evidence
        (e.g. ``"VERIFIED"``, ``"UNVERIFIED"``).

    Returns
    -------
    dict[str, Any]
        A structured evidence record with assignments, provenance, and metadata.
    """
    reconstruction_id = str(uuid.uuid4())
    reconstruction_timestamp = time.time()

    # Build provenance list: one entry per variable, recording extraction time
    provenance: list[str] = [
        f"extracted_{k}_at_{reconstruction_timestamp}"
        for k in model_dict.keys()
    ]

    # Group assignments by Python type for downstream consumers
    type_groups: dict[str, dict[str, Any]] = {
        "bool": {},
        "int": {},
        "float": {},
        "str": {},
        "other": {},
    }

    for key, value in model_dict.items():
        if isinstance(value, bool):
            # bool check must come before int since bool is a subclass of int
            type_groups["bool"][key] = value
        elif isinstance(value, int):
            type_groups["int"][key] = value
        elif isinstance(value, float):
            type_groups["float"][key] = value
        elif isinstance(value, str):
            type_groups["str"][key] = value
        else:
            type_groups["other"][key] = value

    # Compute a content hash for integrity checking
    content_hash = hashlib.sha256(
        json.dumps(model_dict, sort_keys=True, default=str).encode()
    ).hexdigest()

    evidence = {
        "reconstruction_id": reconstruction_id,
        "query_id": query_id,
        "trust_level": trust_level,
        "assignments": dict(model_dict),
        "type_groups": type_groups,
        "provenance": provenance,
        "reconstructed_at": reconstruction_timestamp,
        "variable_count": len(model_dict),
        "content_hash": content_hash,
        "type_summary": {
            group: len(entries)
            for group, entries in type_groups.items()
        },
    }
    return evidence


# ---------------------------------------------------------------------------


def compute_branch_sensitivity(
    branch_conditions: list[str],
    model_dict: dict[str, Any],
) -> dict[str, Any]:
    """Determine which branch conditions are active under a given model.

    Implements the branch sensitivity analysis from Ch31 §31.5: for each
    branch condition string, check whether the model assigns a truthy value
    to the corresponding variable.

    The heuristic is:
    - If the condition string appears as a key in *model_dict*, use
      ``bool(model_dict[condition])`` to determine activity.
    - Otherwise the branch is considered inactive (conservatively).

    Parameters
    ----------
    branch_conditions:
        List of condition strings (e.g. variable names or SMT2 identifiers)
        that correspond to branch guards in the encoded program.
    model_dict:
        Key-value assignments from a Z3 satisfying model.

    Returns
    -------
    dict[str, Any]
        - ``active_branches``   : list of indices of active conditions
        - ``inactive_branches`` : list of indices of inactive conditions
        - ``sensitivity_map``   : mapping from condition string to bool
        - ``branch_count``      : total number of branch conditions
        - ``active_count``      : number of active branches
    """
    if not branch_conditions:
        return {
            "active_branches": [],
            "inactive_branches": [],
            "sensitivity_map": {},
            "branch_count": 0,
            "active_count": 0,
            "coverage_ratio": 0.0,
        }

    # Build a mapping: condition -> bool (is it active under the model?)
    sensitivity_map: dict[str, bool] = {}
    for cond in branch_conditions:
        if cond in model_dict:
            # Directly evaluate the model value as a boolean
            raw_value = model_dict[cond]
            if isinstance(raw_value, bool):
                sensitivity_map[cond] = raw_value
            elif isinstance(raw_value, str):
                # Handle Z3 string representations like "true"/"false"
                sensitivity_map[cond] = raw_value.lower() == "true"
            elif isinstance(raw_value, (int, float)):
                sensitivity_map[cond] = bool(raw_value)
            else:
                # Default: treat as active if value is truthy
                sensitivity_map[cond] = bool(raw_value)
        else:
            # Condition not present in model — treat as inactive
            sensitivity_map[cond] = False

    # Compute active/inactive index lists
    active_branches = [
        i for i, cond in enumerate(branch_conditions) if sensitivity_map.get(cond, False)
    ]
    inactive_branches = [
        i for i in range(len(branch_conditions)) if i not in active_branches
    ]

    branch_count = len(branch_conditions)
    active_count = len(active_branches)
    coverage_ratio = active_count / branch_count if branch_count > 0 else 0.0

    return {
        "active_branches": active_branches,
        "inactive_branches": inactive_branches,
        "sensitivity_map": sensitivity_map,
        "branch_count": branch_count,
        "active_count": active_count,
        "coverage_ratio": coverage_ratio,
        "active_conditions": [branch_conditions[i] for i in active_branches],
        "inactive_conditions": [branch_conditions[i] for i in inactive_branches],
    }


# ---------------------------------------------------------------------------


def totalize_partial(
    partial_smt2: str,
    domain_pred_smt2: str,
    default_value: str,
    arg_name: str,
) -> str:
    """Produce a total SMT2 expression from a partial one by providing a default.

    Implements the totalization construction from Ch31 §31.1:

    .. math::

       \\hat{f}(x) = \\mathbf{ite}(\\mathrm{dom}_f(x),\\, f(x),\\, d)

    where *d* is the chosen default element.

    Parameters
    ----------
    partial_smt2:
        The name or expression for the partial function in SMT2.
    domain_pred_smt2:
        The name of the domain predicate function in SMT2.
    default_value:
        The SMT2 expression to use when the argument is outside the domain.
    arg_name:
        The variable name to substitute for the function argument.

    Returns
    -------
    str
        A comment line followed by the totalized ``ite`` expression.
    """
    # Validate inputs
    for param_name, param_val in [
        ("partial_smt2", partial_smt2),
        ("domain_pred_smt2", domain_pred_smt2),
        ("default_value", default_value),
        ("arg_name", arg_name),
    ]:
        if not isinstance(param_val, str) or not param_val.strip():
            raise ValueError(
                f"totalize_partial: '{param_name}' must be a non-empty string, got {param_val!r}"
            )

    # Build the ite expression
    ite_expr = (
        f"(ite ({domain_pred_smt2} {arg_name})"
        f" ({partial_smt2} {arg_name})"
        f" {default_value})"
    )

    # Prepend an explanatory comment
    comment = (
        f"; Totalized {partial_smt2} with default {default_value}\n"
        f"; Domain predicate: {domain_pred_smt2}\n"
        f"; Argument variable: {arg_name}\n"
        f"; Generated by totalize_partial (Ch31 §31.1)"
    )

    return f"{comment}\n{ite_expr}"


# ---------------------------------------------------------------------------


def merge_reconstructed_models(
    models: list[dict[str, Any]],
    strategy: MergeStrategy,
) -> dict[str, Any]:
    """Merge multiple reconstructed Z3 models according to a chosen strategy.

    When multiple satisfying models are available (e.g. from multiple solver
    calls or from parallel queries), this function combines them into a single
    model according to the specified :class:`MergeStrategy`.

    Parameters
    ----------
    models:
        List of model dictionaries to merge.
    strategy:
        The merge strategy to use.

    Returns
    -------
    dict[str, Any]
        - ``merged``         : the merged assignment dictionary
        - ``count``          : number of input models
        - ``strategy``       : the strategy value string
        - ``conflicts_found``: number of conflicting key-value pairs detected
        - ``merged_at``      : UNIX timestamp of the merge

    Raises
    ------
    ValueError
        Only if strategy is CONFLICT_FAIL and a conflict is detected.
        This error is caught internally and recorded in the ``errors`` list.
    """
    if not models:
        return {
            "merged": {},
            "count": 0,
            "strategy": strategy.value,
            "conflicts_found": 0,
            "errors": [],
            "merged_at": time.time(),
        }

    errors: list[str] = []
    conflict_count = 0

    if strategy == MergeStrategy.LEFT_BIASED:
        # Start with first model; add keys from subsequent models only if absent
        merged: dict[str, Any] = dict(models[0])
        for subsequent in models[1:]:
            for key, value in subsequent.items():
                if key in merged and merged[key] != value:
                    conflict_count += 1
                    # Left wins — do nothing
                elif key not in merged:
                    merged[key] = value

    elif strategy == MergeStrategy.RIGHT_BIASED:
        # Reverse the list, then apply LEFT_BIASED logic
        reversed_models = list(reversed(models))
        merged = dict(reversed_models[0])
        for subsequent in reversed_models[1:]:
            for key, value in subsequent.items():
                if key in merged and merged[key] != value:
                    conflict_count += 1
                elif key not in merged:
                    merged[key] = value

    elif strategy == MergeStrategy.UNION:
        # All values are kept; conflicting values become lists
        merged = {}
        for model in models:
            for key, value in model.items():
                if key not in merged:
                    merged[key] = value
                elif merged[key] != value:
                    # Convert to list if not already
                    if not isinstance(merged[key], list):
                        merged[key] = [merged[key]]
                    if value not in merged[key]:
                        merged[key].append(value)
                    conflict_count += 1

    elif strategy == MergeStrategy.INTERSECTION:
        # Keep only keys that appear in ALL models with the SAME value
        if not models:
            merged = {}
        else:
            # Start from the set of keys in the first model
            common_keys = set(models[0].keys())
            for model in models[1:]:
                common_keys &= set(model.keys())

            merged = {}
            for key in common_keys:
                values = [m[key] for m in models]
                # Only include if all values are identical
                if all(v == values[0] for v in values):
                    merged[key] = values[0]
                else:
                    conflict_count += 1

    elif strategy == MergeStrategy.CONFLICT_FAIL:
        # Like LEFT_BIASED but raise on conflict
        merged = dict(models[0])
        for model_index, subsequent in enumerate(models[1:], start=1):
            for key, value in subsequent.items():
                if key in merged:
                    if merged[key] != value:
                        conflict_count += 1
                        error_msg = (
                            f"CONFLICT_FAIL: key '{key}' has conflicting values "
                            f"{merged[key]!r} (model 0) vs {value!r} (model {model_index})"
                        )
                        errors.append(error_msg)
                        # We record the error but continue so all conflicts are reported
                else:
                    merged[key] = value
    else:
        # Fallback — should never reach here but handle defensively
        merged = dict(models[0]) if models else {}
        errors.append(f"Unknown strategy: {strategy!r}; fell back to LEFT_BIASED")

    return {
        "merged": merged,
        "count": len(models),
        "strategy": strategy.value,
        "conflicts_found": conflict_count,
        "errors": errors,
        "keys_in_merged": len(merged),
        "merged_at": time.time(),
    }


# ---------------------------------------------------------------------------


def validate_model_faithfulness(
    model_dict: dict[str, Any],
    constraints: list[str],
) -> dict[str, Any]:
    """Check that a reconstructed model is faithful to a set of string constraints.

    Implements the faithfulness check from Ch31 §31.4: for each constraint
    string, identify the variable names it references and verify that all
    referenced variables have assignments in the model.

    The variable extraction heuristic uses a simple word-boundary regex:
    any token matching ``[A-Za-z_][A-Za-z0-9_]*`` that is not an SMT2
    keyword is treated as a potential variable name.

    Parameters
    ----------
    model_dict:
        Reconstructed key-value assignments to validate.
    constraints:
        List of constraint strings (e.g. SMT2 expressions or Python conditions).

    Returns
    -------
    dict[str, Any]
        - ``is_faithful``  : True iff all constraints have all vars in the model
        - ``violations``   : list of violating constraint strings
        - ``score``        : float in [0, 1] measuring proportion of satisfied constraints
        - ``details``      : per-constraint status dict
        - ``validated_at`` : UNIX timestamp
    """
    # SMT2 keywords and common tokens that should not be treated as variable names
    SMT2_KEYWORDS: frozenset[str] = frozenset({
        "assert", "declare", "define", "fun", "forall", "exists", "let", "ite",
        "and", "or", "not", "true", "false", "Int", "Bool", "Real", "String",
        "Array", "BitVec", "check", "sat", "unsat", "push", "pop", "get",
        "value", "model", "set", "option", "logic",
        "=>", "=", "+", "-", "*", "/", "<", ">", "<=", ">=", "distinct",
    })

    # Pre-compile a regex for identifier-like tokens
    ident_pattern = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\b')

    violations: list[str] = []
    details: dict[str, str] = {}

    for constraint in constraints:
        # Extract all identifier-like tokens from the constraint
        tokens = ident_pattern.findall(constraint)
        # Filter out SMT2 keywords
        candidate_vars = [t for t in tokens if t not in SMT2_KEYWORDS]

        # Check whether all candidate variable names appear in the model
        missing_vars = [v for v in candidate_vars if v not in model_dict]

        if missing_vars:
            violations.append(constraint)
            details[constraint] = (
                f"violation: vars {missing_vars} not in model"
            )
        else:
            details[constraint] = "ok"

    total = len(constraints)
    num_violations = len(violations)
    score = (total - num_violations) / max(total, 1)

    return {
        "is_faithful": len(violations) == 0,
        "violations": violations,
        "score": score,
        "details": details,
        "total_constraints": total,
        "satisfied_count": total - num_violations,
        "violation_count": num_violations,
        "validated_at": time.time(),
    }


# ---------------------------------------------------------------------------
# AlgorithmRegistry
# ---------------------------------------------------------------------------


class AlgorithmRegistry:
    """Registry and dispatcher for Ch31 core algorithms.

    Maintains a dictionary of named algorithm functions and provides a
    uniform interface for running them with timing and error wrapping.

    The registry is pre-populated with all seven core Ch31 algorithms
    on construction.

    Example
    -------
    >>> registry = AlgorithmRegistry()
    >>> result = registry.run(
    ...     "encode_partial_function",
    ...     name="f", domain_sort="Int", range_sort="Int",
    ...     guard_expr="(>= x 0)", body_expr="(* x x)",
    ... )
    >>> result.is_success()
    True
    """

    def __init__(self) -> None:
        """Initialise the registry and register all core Ch31 algorithms."""
        self._algorithms: dict[str, Any] = {}

        # Register the seven core algorithms from Ch31
        self.register("encode_partial_function", encode_partial_function)
        self.register("decode_z3_model_to_surface", decode_z3_model_to_surface)
        self.register("reconstruct_evidence_from_model", reconstruct_evidence_from_model)
        self.register("compute_branch_sensitivity", compute_branch_sensitivity)
        self.register("totalize_partial", totalize_partial)
        self.register("merge_reconstructed_models", merge_reconstructed_models)
        self.register("validate_model_faithfulness", validate_model_faithfulness)

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register(self, name: str, func: Any) -> None:
        """Register an algorithm function under the given name.

        Parameters
        ----------
        name:
            The key under which the function is stored and looked up.
        func:
            Any callable to register as an algorithm.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"AlgorithmRegistry.register: name must be a non-empty string, got {name!r}")
        self._algorithms[name] = func

    def run(self, name: str, **kwargs: Any) -> AlgorithmResult:
        """Look up and execute a registered algorithm by name.

        Wraps the execution in an :class:`AlgorithmResult` with timing
        and error capture.

        Parameters
        ----------
        name:
            The registered algorithm name.
        **kwargs:
            Keyword arguments forwarded to the algorithm function.

        Returns
        -------
        AlgorithmResult
            The result of the algorithm execution.  If the algorithm is not
            found or raises an exception, the result has FAILED status.
        """
        if name not in self._algorithms:
            return AlgorithmResult(
                status=AlgorithmStatus.FAILED,
                result=None,
                errors=[f"Algorithm '{name}' is not registered.  Available: {self.list_algorithms()}"],
                algorithm_name=name,
            )

        func = self._algorithms[name]
        start_time = time.perf_counter()

        try:
            raw_result = func(**kwargs)
            elapsed = time.perf_counter() - start_time

            # Determine whether the result itself contains error indicators
            warnings: list[str] = []
            if isinstance(raw_result, dict):
                result_errors = raw_result.get("errors", [])
                if result_errors:
                    warnings.extend(str(e) for e in result_errors)

            status = (
                AlgorithmStatus.PARTIAL_SUCCESS if warnings else AlgorithmStatus.SUCCESS
            )
            return AlgorithmResult(
                status=status,
                result=raw_result,
                warnings=warnings,
                execution_time=elapsed,
                algorithm_name=name,
            )

        except ValueError as exc:
            elapsed = time.perf_counter() - start_time
            return AlgorithmResult(
                status=AlgorithmStatus.FAILED,
                result=None,
                errors=[f"ValueError in '{name}': {exc}"],
                execution_time=elapsed,
                algorithm_name=name,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - start_time
            return AlgorithmResult(
                status=AlgorithmStatus.FAILED,
                result=None,
                errors=[f"Unexpected error in '{name}': {type(exc).__name__}: {exc}"],
                execution_time=elapsed,
                algorithm_name=name,
            )

    def list_algorithms(self) -> list[str]:
        """Return a sorted list of all registered algorithm names.

        Returns
        -------
        list[str]
            Sorted list of algorithm name strings.
        """
        return sorted(self._algorithms.keys())

    def __repr__(self) -> str:
        alg_count = len(self._algorithms)
        names = self.list_algorithms()
        return f"AlgorithmRegistry({alg_count} algorithms: {names})"

    def __len__(self) -> int:
        return len(self._algorithms)

    def __contains__(self, name: str) -> bool:
        return name in self._algorithms


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "AlgorithmStatus",
    "MergeStrategy",
    "ValidationLevel",
    # Dataclasses
    "AlgorithmResult",
    # Classes
    "AlgorithmRegistry",
    # Core algorithm functions
    "encode_partial_function",
    "decode_z3_model_to_surface",
    "reconstruct_evidence_from_model",
    "compute_branch_sensitivity",
    "totalize_partial",
    "merge_reconstructed_models",
    "validate_model_faithfulness",
]
