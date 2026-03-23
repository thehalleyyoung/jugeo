"""jugeo.encodings.sequence_mutation_encodings
==============================================

Exact Z3 encodings for sequences, finite maps, heap slices, and
support-aware mutation — Theory2.tex Chapter 29.

This package implements Chapter 29 §1–§5 of theory2.tex:

    §1  Structured-data encoder       — lists/tuples/dicts as typed Z3 arrays
    §2  Sequence-window encoder       — window/slice predicates (∀ i∈[lo,hi))
    §3  Finite-map encoder            — Python dicts as Z3 partial functions
    §4  Heap-slice encoder            — localized heap summaries with frames
    §5  Mutation countermodel encoder — repair guides extracted from UNSAT cores

Theoretical anchor
------------------
Chapter 29 ("Exact Z3 encodings IV: sequences, finite maps, heap slices,
support-aware mutation") develops the formal bridge between Python-level
mutable data structures and their theory-safe Z3 representations.  The key
insight is that every mutation can be decomposed over a *support set* —  a
finite set of addresses / indices outside which the pre- and post-states agree
(the *frame axiom*).  This package implements that decomposition and the
associated encoding machinery.

Public surface — models
-----------------------
.. autoclass:: SequenceEncoding
.. autoclass:: MutationSlice
.. autoclass:: HeapSlice
.. autoclass:: SupportAwareMutation
.. autoclass:: SequenceInvariant

Public surface — encoders
--------------------------
.. autoclass:: StructuredDataEncoder
.. autoclass:: SequenceWindowEncoder
.. autoclass:: FiniteMapEncoder
.. autoclass:: HeapSliceEncoder
.. autoclass:: MutationCountermodelEncoder

Public surface — algorithms & integration
-----------------------------------------
.. autofunction:: sequence_induction_schema
.. autofunction:: build_support_closure
.. autofunction:: decompose_mutation_by_support
.. autoclass:: SequenceMutationSolverIntegration

Public surface — theorems
--------------------------
.. autoclass:: SequenceMutationTheorem
.. autoclass:: FramePreservationTheorem
.. autoclass:: SupportClosureTheorem
.. autoclass:: MutationCompositionTheorem
.. autoclass:: HeapSliceConsistencyTheorem
.. autoclass:: InvariantRepairTheorem

Usage example
-------------
::

    from jugeo.encodings.sequence_mutation_encodings import (
        SequenceEncoding,
        StructuredDataEncoder,
        FramePreservationTheorem,
    )
    enc = StructuredDataEncoder()
    seq_enc = enc.encode_list([1, 2, 3], elem_sort='Int')

Notes
-----
* All Z3 imports are guarded with ``try/except ImportError``; symbolic stubs
  are provided when z3-python is unavailable so that import never fails.
* Type annotations use ``TYPE_CHECKING`` guards for heavy imports.

# copilot: package init for sequence_mutation_encodings — Theory2.tex Ch29.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.models import (
    HeapSlice,
    MutationKind,
    MutationSlice,
    SequenceEncoding,
    SequenceInvariant,
    SequenceInvariantKind,
    SupportAwareMutation,
)

# ---------------------------------------------------------------------------
# Encoders
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.structured_data_encoder import (
    StructuredDataEncoder,
    EncodedList,
    EncodedTuple,
)
from jugeo.encodings.sequence_mutation_encodings.sequence_window_encoder import (
    SequenceWindowEncoder,
    WindowPredicate,
)
from jugeo.encodings.sequence_mutation_encodings.finite_map_encoder import (
    EncodedMap,
    FiniteMapEncoder,
)
from jugeo.encodings.sequence_mutation_encodings.heap_slice_encoder import (
    HeapSliceEncoder,
    EncodedHeapSlice,
)
from jugeo.encodings.sequence_mutation_encodings.mutation_countermodel_encoder import (
    MutationCountermodelEncoder,
    RepairSuggestion,
    ViolationContext,
)

# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.algorithms import (
    abstractly_interpret_mutation,
    build_support_closure,
    check_frame_preservation,
    compute_mutation_footprint,
    copilot_derive_loop_invariant,
    decompose_mutation_by_support,
    repair_invariant_violation,
    sequence_induction_schema,
    sequence_window_widening,
    unify_heap_slices,
)

# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.integration import (
    SequenceMutationSolverIntegration,
)

# ---------------------------------------------------------------------------
# Theorems
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.theorems import (
    FramePreservationTheorem,
    HeapSliceConsistencyTheorem,
    InvariantRepairTheorem,
    MutationCompositionTheorem,
    SequenceMutationTheorem,
    SupportClosureTheorem,
)

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
from jugeo.encodings.sequence_mutation_encodings.manifest import (
    SEQUENCE_MUTATION_MANIFEST,
    get_manifest,
)

__all__: list[str] = [
    # models
    "SequenceEncoding",
    "MutationSlice",
    "HeapSlice",
    "SupportAwareMutation",
    "SequenceInvariant",
    "MutationKind",
    "SequenceInvariantKind",
    # encoders
    "StructuredDataEncoder",
    "EncodedList",
    "EncodedTuple",
    "SequenceWindowEncoder",
    "WindowPredicate",
    "FiniteMapEncoder",
    "EncodedMap",
    "HeapSliceEncoder",
    "EncodedHeapSlice",
    "MutationCountermodelEncoder",
    "RepairSuggestion",
    "ViolationContext",
    # algorithms
    "sequence_induction_schema",
    "build_support_closure",
    "decompose_mutation_by_support",
    "unify_heap_slices",
    "check_frame_preservation",
    "compute_mutation_footprint",
    "repair_invariant_violation",
    "sequence_window_widening",
    "abstractly_interpret_mutation",
    "copilot_derive_loop_invariant",
    # integration
    "SequenceMutationSolverIntegration",
    # theorems
    "SequenceMutationTheorem",
    "FramePreservationTheorem",
    "SupportClosureTheorem",
    "MutationCompositionTheorem",
    "HeapSliceConsistencyTheorem",
    "InvariantRepairTheorem",
    # manifest
    "SEQUENCE_MUTATION_MANIFEST",
    "get_manifest",
]


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import finite_map_encoder
except Exception:
    pass
try:
    from . import finite_maps_and_interface_dictiona
except Exception:
    pass
try:
    from . import heap_slice_encoder
except Exception:
    pass
try:
    from . import heap_slices_and_mutation_support
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import mutation_countermodel_encoder
except Exception:
    pass
try:
    from . import mutation_countermodels_as_repair_g
except Exception:
    pass
try:
    from . import mutation_countermodels_as_repair_new
except Exception:
    pass
try:
    from . import sequence_window_encoder
except Exception:
    pass
try:
    from . import sequence_windows
except Exception:
    pass
try:
    from . import structured_data_encoder
except Exception:
    pass
try:
    from . import structured_data_should_not_be_flat
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
