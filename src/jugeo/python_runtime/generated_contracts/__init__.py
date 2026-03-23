"""Package scaffold for JuGeo generated modules."""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════════════
# Cross-subsystem functions
# ══════════════════════════════════════════════════════════════════════════════


def contract_judgment(contract: object) -> dict:
    """Create a judgment section for a generated contract.

    Uses :mod:`jugeo.judgments.sections` to build a judgment section that
    records the contract's pre/post conditions and invariants.

    Parameters
    ----------
    contract : object
        A contract record or mapping.

    Returns
    -------
    dict
        A judgment section dict.
    """
    try:
        from jugeo.judgments.sections import make_section
    except ImportError:
        return {
            "section_type": "contract_judgment",
            "contract": str(contract),
        }

    return make_section(
        section_type="contract_judgment",
        coordinate=getattr(contract, "name", str(contract)),
        bindings={
            "preconditions": getattr(contract, "preconditions", []),
            "postconditions": getattr(contract, "postconditions", []),
            "invariants": getattr(contract, "invariants", []),
        },
        trust_level=getattr(contract, "trust_level", 0),
    )


def contract_encoding(contract: object) -> object:
    """Encode a generated contract for Z3 constraint solving.

    Uses :mod:`jugeo.encodings.scalar_encodings` to produce a Z3-compatible
    encoding of the contract's constraint counts and satisfaction status.

    Parameters
    ----------
    contract : object
        A contract record.

    Returns
    -------
    object
        A Z3 encoding, or *None* if the encoding layer is unavailable.
    """
    try:
        from jugeo.encodings.scalar_encodings import encode_scalar
    except ImportError:
        return None

    name = getattr(contract, "name", "contract")
    clause_count = (
        len(getattr(contract, "preconditions", []))
        + len(getattr(contract, "postconditions", []))
        + len(getattr(contract, "invariants", []))
    )
    return encode_scalar(
        label=f"contract_{name}",
        value=clause_count,
        domain="nat",
    )


def contract_certificate(contract: object) -> object:
    """Issue a verification certificate for a generated contract.

    Uses :mod:`jugeo.evidence.certificates` to create a certificate
    attesting to the contract's verification status.

    Parameters
    ----------
    contract : object
        A contract record.

    Returns
    -------
    object
        A certificate object, or a fallback dict.
    """
    try:
        from jugeo.evidence.certificates import issue_certificate
    except ImportError:
        return {
            "certificate_type": "contract",
            "contract": str(contract),
            "status": "unverified",
        }

    return issue_certificate(
        certificate_type="contract",
        subject=getattr(contract, "name", str(contract)),
        claims={
            "preconditions": len(getattr(contract, "preconditions", [])),
            "postconditions": len(getattr(contract, "postconditions", [])),
            "invariants": len(getattr(contract, "invariants", [])),
        },
    )


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import annotations_as_latent_behavior
except Exception:
    pass
try:
    from . import generated_contracts
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
    from . import registries
except Exception:
    pass
try:
    from . import registry_surfaces
except Exception:
    pass
try:
    from . import theorem_burden
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
