"""Core contract machinery for JuGeo library contracts.

A *library contract* is a sheaf-theoretic annotation: it adds propositions
to the coordinate of a call site, allowing descent to verify shape safety,
type safety, or behavioral correctness even when the callee's source is
unavailable (e.g., ``torch.matmul``, ``numpy.dot``).

Sheaf interpretation:
    For a call ``y = f(x)`` at coordinate ``c``:
      - Each ``@requires`` becomes a local section at ``c`` demanding the
        pre-condition holds.
      - Each ``@ensures`` becomes a local section at ``c`` guaranteeing the
        post-condition holds (assuming the pre-conditions are met).
      - The contract's restriction morphism maps from the caller coordinate
        to the callee boundary, enabling descent across module boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class Contract:
    """A single function contract: pre-conditions, post-conditions, invariants."""
    qualified_name: str
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    description: str = ""

    @property
    def n_obligations(self) -> int:
        return len(self.preconditions) + len(self.postconditions) + len(self.invariants)


class ContractRegistry:
    """Global registry of library contracts."""

    _contracts: Dict[str, Contract] = {}

    @classmethod
    def register(cls, contract: Contract) -> None:
        cls._contracts[contract.qualified_name] = contract

    @classmethod
    def get(cls, name: str) -> Optional[Contract]:
        return cls._contracts.get(name)

    @classmethod
    def all(cls) -> Dict[str, Contract]:
        return dict(cls._contracts)

    @classmethod
    def clear(cls) -> None:
        cls._contracts.clear()

    @classmethod
    def summary(cls) -> str:
        lines = [f"ContractRegistry: {len(cls._contracts)} contracts"]
        for name, c in sorted(cls._contracts.items()):
            lines.append(
                f"  {name}: {len(c.preconditions)} pre, "
                f"{len(c.postconditions)} post"
            )
        return "\n".join(lines)


def get_registry() -> ContractRegistry:
    return ContractRegistry


# ─── Decorators ───────────────────────────────────────────────────

def requires(condition: str):
    """Add a pre-condition to a contract function."""
    def decorator(fn: Callable) -> Callable:
        if not hasattr(fn, '_jugeo_pre'):
            fn._jugeo_pre: list[str] = []
        fn._jugeo_pre.append(condition)
        return fn
    return decorator


def ensures(condition: str):
    """Add a post-condition to a contract function."""
    def decorator(fn: Callable) -> Callable:
        if not hasattr(fn, '_jugeo_post'):
            fn._jugeo_post: list[str] = []
        fn._jugeo_post.append(condition)
        return fn
    return decorator


def invariant(condition: str):
    """Add a class invariant to a contract."""
    def decorator(fn: Callable) -> Callable:
        if not hasattr(fn, '_jugeo_inv'):
            fn._jugeo_inv: list[str] = []
        fn._jugeo_inv.append(condition)
        return fn
    return decorator


def library_contract(qualified_name: str, description: str = ""):
    """Register a library function contract with JuGeo.

    Usage::

        @library_contract("torch.matmul", "Matrix multiplication")
        @requires("A.shape[-1] == B.shape[-2]")
        @ensures("result.shape == (*A.shape[:-1], B.shape[-1])")
        def matmul_contract(A, B): ...
    """
    def decorator(fn: Callable) -> Callable:
        c = Contract(
            qualified_name=qualified_name,
            preconditions=tuple(getattr(fn, '_jugeo_pre', [])),
            postconditions=tuple(getattr(fn, '_jugeo_post', [])),
            invariants=tuple(getattr(fn, '_jugeo_inv', [])),
            description=description or fn.__doc__ or "",
        )
        ContractRegistry.register(c)
        fn._jugeo_contract = c
        return fn
    return decorator
