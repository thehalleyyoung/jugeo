"""JuGeo contract system — pre/post conditions for library functions.

Contracts tell JuGeo what a library function guarantees without needing
its source code.  When ``jugeo prove`` encounters a call to a contracted
function, it uses the contract to generate propositions at that coordinate.

Usage::

    from jugeo.contracts import library_contract, requires, ensures

    @library_contract("torch.matmul")
    @requires("A.shape[-1] == B.shape[-2]")
    @ensures("result.shape == (*A.shape[:-1], B.shape[-1])")
    def matmul(A, B): ...

The contract registry is available via ``get_registry()``.
"""

from jugeo.contracts.core import (
    Contract,
    ContractRegistry,
    ensures,
    get_registry,
    invariant,
    library_contract,
    requires,
)

__all__ = [
    "Contract",
    "ContractRegistry",
    "requires",
    "ensures",
    "invariant",
    "library_contract",
    "get_registry",
]
