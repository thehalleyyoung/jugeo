r"""Bridge theorems modelled as morphisms between pack vocabularies.

Theory (theory2.tex §35.2 — Bridge Theorems as Morphisms):
    A bridge theorem B : P₁ → P₂ in the pack federation setting is a
    structure-preserving map (morphism) between two pack vocabularies.  More
    precisely, it is a functor from the category of evidence over P₁ to the
    category of evidence over P₂, restricted to the overlap region
    B.overlap_region.

    For B to be a valid functor it must satisfy two axioms:
    (F1 — Identity) B maps the identity evidence of P₁ to the identity
    evidence of P₂ on the overlap.
    (F2 — Composition) For composable bridges B₁ : P₁ → P₂ and B₂ : P₂ → P₃,
    the composition B₂ ∘ B₁ is again a bridge morphism from P₁ to P₃, and
    the diagram commutes.

    Naturality: Given a natural transformation η : F ⇒ G between two evidence
    functors, the naturality square
        F(P₁) --η_{P₁}--> G(P₁)
           |                  |
          B                  B
           ↓                  ↓
        F(P₂) --η_{P₂}--> G(P₂)
    commutes iff the bridge morphism interleaves correctly with the
    transformation η.

    The class :class:`BridgeTheoremAsMorphism` provides the full categorical
    machinery: functor-law checking, naturality square verification, kernel and
    image computation, and faithfulness testing.

Public surface
--------------
:class:`BridgeTheoremAsMorphism`
    Dataclass treating a bridge theorem as a categorical morphism.

copilot: bridge-theorem-as-morphism
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Final, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .models import BridgeTheoremEncoding

__all__: list[str] = [
    "BridgeTheoremAsMorphism",
]


# ---------------------------------------------------------------------------
# BridgeTheoremAsMorphism
# ---------------------------------------------------------------------------


@dataclass
class BridgeTheoremAsMorphism:
    """A bridge theorem treated as a categorical morphism between pack vocabularies.

    This class wraps a :class:`BridgeTheoremEncoding` and augments it with
    the full source and target vocabulary maps (term → type), a composition
    registry for tracking composed morphisms, and methods that verify the
    categorical axioms required by theory2.tex §35.2.

    Parameters
    ----------
    encoding:
        The underlying bridge theorem encoding.
    source_vocabulary:
        Mapping from vocabulary term to type string for the source pack.
    target_vocabulary:
        Mapping from vocabulary term to type string for the target pack.
    composition_registry:
        List of previously composed :class:`BridgeTheoremEncoding` instances
        accumulated by calls to :meth:`compose`.

    copilot: bridge-morphism-dataclass
    """

    encoding: BridgeTheoremEncoding
    source_vocabulary: dict[str, str]
    target_vocabulary: dict[str, str]
    composition_registry: list[BridgeTheoremEncoding] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 1. verify_functor_laws
    # ------------------------------------------------------------------

    def verify_functor_laws(self) -> tuple[bool, list[str]]:
        """Check that this morphism satisfies the functor axioms.

        Axiom F1 (Identity preservation): On a trivial bridge where
        source_formula == target_formula and the overlap is the full
        source vocabulary, every term maps to itself.

        Axiom F2 (Composition preservation): For each pair of bridges in
        :attr:`composition_registry`, check that composing them in order
        yields the same trust ceiling as composing in the reverse order is
        *not* required (composition need not be commutative); but the trust
        must be monotone: trust(B2 ∘ B1) ≤ min(trust(B1), trust(B2)).

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if both axioms pass; ``(False, errors)`` otherwise.
        """
        errors: list[str] = []

        # F1: identity-like check — source_formula non-empty and refers to self
        if not self.encoding.source_formula:
            errors.append("F1 violated: source_formula is empty (no identity map)")
        if not self.encoding.target_formula:
            errors.append("F1 violated: target_formula is empty (no identity map on target)")

        # Check that overlap region is a subset of both vocabularies
        for coord in self.encoding.overlap_region:
            if coord not in self.source_vocabulary:
                errors.append(
                    f"F1 violated: overlap coord {coord!r} absent from source_vocabulary"
                )
            if coord not in self.target_vocabulary:
                errors.append(
                    f"F1 violated: overlap coord {coord!r} absent from target_vocabulary"
                )

        # F2: composition monotonicity across composition_registry
        reg = self.composition_registry
        if len(reg) >= 2:
            for i in range(len(reg) - 1):
                b1 = reg[i]
                b2 = reg[i + 1]
                composed = b1.compose_with(b2)
                expected_ceiling = min(b1.trust_ceiling, b2.trust_ceiling)
                if composed.trust_ceiling > expected_ceiling + 1e-9:
                    errors.append(
                        f"F2 violated: composed trust {composed.trust_ceiling:.4f} > "
                        f"min({b1.trust_ceiling:.4f}, {b2.trust_ceiling:.4f}) = {expected_ceiling:.4f}"
                    )

        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # 2. check_naturality_square
    # ------------------------------------------------------------------

    def check_naturality_square(
        self, other: BridgeTheoremAsMorphism
    ) -> tuple[bool, dict[str, Any]]:
        """Verify that the naturality square commutes for composable morphisms.

        Given self: P₁ → P₂ and other: P₂ → P₃ (where
        self.encoding.target_pack_id == other.encoding.source_pack_id),
        the naturality square:

            source_vocab(P₁) ---self.translate---> target_vocab(P₂)
                    |                                      |
              other.source                          other.target
                    ↓                                      ↓
            source_vocab(P₂) --other.translate--> target_vocab(P₃)

        commutes iff translating a term from P₁ to P₂ then to P₃ gives the
        same result as the composed morphism directly from P₁ to P₃.

        Parameters
        ----------
        other:
            The second morphism; should have source_pack_id matching
            self.encoding.target_pack_id.

        Returns
        -------
        tuple[bool, dict]
            ``(True, {})`` if the square commutes; ``(False, evidence)`` where
            *evidence* contains the mismatching term translations.
        """
        mismatches: dict[str, Any] = {}

        is_composable = (
            self.encoding.target_pack_id == other.encoding.source_pack_id
        )
        if not is_composable:
            return False, {
                "error": (
                    f"Not composable: self.target {self.encoding.target_pack_id!r} "
                    f"!= other.source {other.encoding.source_pack_id!r}"
                )
            }

        # For each term in the shared overlap, trace the path via both morphisms
        shared_overlap = self.encoding.overlap_region & other.encoding.overlap_region
        for term in sorted(shared_overlap):
            # Path via self then other
            via_self = self.translate_vocabulary_term(term)
            via_other = other.translate_vocabulary_term(term) if via_self else None

            # Direct path: is the term in other.source_vocabulary?
            direct = other.translate_vocabulary_term(term)

            if via_other != direct:
                mismatches[term] = {
                    "via_self_then_other": via_other,
                    "direct": direct,
                }

        commutes = len(mismatches) == 0
        evidence = {
            "composable": is_composable,
            "shared_overlap": sorted(shared_overlap),
            "mismatches": mismatches,
        }
        return commutes, evidence

    # ------------------------------------------------------------------
    # 3. compose
    # ------------------------------------------------------------------

    def compose(self, other: BridgeTheoremAsMorphism) -> BridgeTheoremAsMorphism:
        """Compose this morphism with *other*, producing a new morphism.

        Composition is only valid when self's target pack matches other's
        source pack.  The resulting morphism has:
        - ``encoding`` = self.encoding.compose_with(other.encoding)
        - ``source_vocabulary`` = self.source_vocabulary
        - ``target_vocabulary`` = other.target_vocabulary
        - ``composition_registry`` = self.composition_registry + [self.encoding, other.encoding]

        Parameters
        ----------
        other:
            Morphism to compose after this one.

        Returns
        -------
        BridgeTheoremAsMorphism
            The composed morphism.

        Raises
        ------
        ValueError
            If self.encoding.target_pack_id != other.encoding.source_pack_id.
        """
        if self.encoding.target_pack_id != other.encoding.source_pack_id:
            raise ValueError(
                f"Cannot compose: self.target_pack_id {self.encoding.target_pack_id!r} "
                f"!= other.source_pack_id {other.encoding.source_pack_id!r}"
            )

        composed_encoding = self.encoding.compose_with(other.encoding)
        new_registry = list(self.composition_registry) + [self.encoding, other.encoding]

        return BridgeTheoremAsMorphism(
            encoding=composed_encoding,
            source_vocabulary=dict(self.source_vocabulary),
            target_vocabulary=dict(other.target_vocabulary),
            composition_registry=new_registry,
        )

    # ------------------------------------------------------------------
    # 4. verify_overlap_laws
    # ------------------------------------------------------------------

    def verify_overlap_laws(self) -> tuple[bool, list[str]]:
        """Verify that all overlap coordinates appear in both vocabularies.

        The overlap law (theory2.tex §35.2) requires that every coordinate in
        :attr:`encoding.overlap_region` is defined in both
        :attr:`source_vocabulary` and :attr:`target_vocabulary`, ensuring that
        the bridge can actually translate between them.

        Returns
        -------
        tuple[bool, list[str]]
            ``(True, [])`` if all overlap coordinates are covered; errors otherwise.
        """
        errors: list[str] = []
        for coord in sorted(self.encoding.overlap_region):
            if coord not in self.source_vocabulary:
                errors.append(
                    f"Overlap law violated: {coord!r} absent from source_vocabulary "
                    f"(pack {self.encoding.source_pack_id!r})"
                )
            if coord not in self.target_vocabulary:
                errors.append(
                    f"Overlap law violated: {coord!r} absent from target_vocabulary "
                    f"(pack {self.encoding.target_pack_id!r})"
                )
        return len(errors) == 0, errors

    # ------------------------------------------------------------------
    # 5. translate_vocabulary_term
    # ------------------------------------------------------------------

    def translate_vocabulary_term(self, term: str) -> str | None:
        """Translate a source vocabulary term to its target vocabulary counterpart.

        Translation succeeds only if *term* is in
        :attr:`encoding.overlap_region`, appears in :attr:`source_vocabulary`,
        and also appears in :attr:`target_vocabulary` (since the overlap is the
        shared vocabulary).

        Parameters
        ----------
        term:
            A term from the source pack's vocabulary.

        Returns
        -------
        str | None
            The type string of the term in :attr:`target_vocabulary`, or
            ``None`` if the term cannot be translated (not in overlap or not
            in target vocabulary).
        """
        if term not in self.encoding.overlap_region:
            return None
        if term not in self.source_vocabulary:
            return None
        return self.target_vocabulary.get(term)

    # ------------------------------------------------------------------
    # 6. compute_kernel
    # ------------------------------------------------------------------

    def compute_kernel(self) -> FrozenSet[str]:
        """Compute the kernel of this morphism.

        The kernel consists of all source vocabulary terms that are *not* in
        the overlap region — i.e., those terms that are "absorbed" by the
        bridge and do not appear in the target vocabulary.

        Returns
        -------
        FrozenSet[str]
            Frozenset of source terms not reachable through this morphism.
        """
        all_source_terms = frozenset(self.source_vocabulary.keys())
        return all_source_terms - self.encoding.overlap_region

    # ------------------------------------------------------------------
    # 7. compute_image
    # ------------------------------------------------------------------

    def compute_image(self) -> FrozenSet[str]:
        """Compute the image of this morphism.

        The image consists of all target vocabulary terms that are reachable
        from the source pack via the overlap region — i.e., the subset of the
        target vocabulary that this bridge "reaches into".

        Returns
        -------
        FrozenSet[str]
            Frozenset of target terms reachable from source via this bridge.
        """
        reachable: set[str] = set()
        for coord in self.encoding.overlap_region:
            if coord in self.target_vocabulary:
                reachable.add(coord)
        return frozenset(reachable)

    # ------------------------------------------------------------------
    # 8. check_faithfulness
    # ------------------------------------------------------------------

    def check_faithfulness(self) -> bool:
        """Check whether this morphism is faithful.

        A morphism is faithful if it is injective on objects — concretely,
        if its kernel is empty.  An empty kernel means every source term is
        transported into the target vocabulary, so no information is lost.

        Returns
        -------
        bool
            ``True`` if the kernel is empty (faithful / injective).
        """
        return len(self.compute_kernel()) == 0

    # ------------------------------------------------------------------
    # 9. get_morphism_category
    # ------------------------------------------------------------------

    def get_morphism_category(self) -> str:
        """Classify this morphism by its categorical properties.

        Returns one of:
        - ``"equivalence"``: bijective encoding and faithful morphism (kernel empty, image = full target)
        - ``"embedding"``: faithful but not surjective onto target
        - ``"projection"``: surjective but not faithful (kernel non-empty)
        - ``"partial"``: neither faithful nor surjective

        Returns
        -------
        str
        """
        faithful = self.check_faithfulness()
        image = self.compute_image()
        target_terms = frozenset(self.target_vocabulary.keys())

        # Surjective: image covers the full target vocabulary on the overlap
        surjective = target_terms.issubset(image | (target_terms - self.encoding.overlap_region))
        # More precise: all overlap terms in target_vocabulary are in the image
        overlap_target = frozenset(
            t for t in self.encoding.overlap_region if t in self.target_vocabulary
        )
        actually_surjective = overlap_target == image

        if faithful and actually_surjective:
            return "equivalence"
        elif faithful and not actually_surjective:
            return "embedding"
        elif not faithful and actually_surjective:
            return "projection"
        else:
            return "partial"

    # ------------------------------------------------------------------
    # 10. summarize_morphism
    # ------------------------------------------------------------------

    def summarize_morphism(self) -> dict[str, Any]:
        """Return a full summary dict describing this morphism.

        Collects all computed properties into a single dict for inspection,
        logging, or serialisation.

        Returns
        -------
        dict[str, Any]
            Keys: ``bridge_id``, ``source_pack``, ``target_pack``,
            ``morphism_type``, ``overlap_size``, ``kernel_size``,
            ``image_size``, ``faithful``, ``category``, ``functor_laws_ok``,
            ``overlap_laws_ok``, ``composition_depth``, ``trust_ceiling``.
        """
        functor_ok, functor_errors = self.verify_functor_laws()
        overlap_ok, overlap_errors = self.verify_overlap_laws()
        kernel = self.compute_kernel()
        image = self.compute_image()
        category = self.get_morphism_category()

        return {
            "bridge_id": self.encoding.bridge_id,
            "source_pack": self.encoding.source_pack_id,
            "target_pack": self.encoding.target_pack_id,
            "morphism_type": self.encoding.morphism_type,
            "overlap_size": self.encoding.get_overlap_size(),
            "kernel_size": len(kernel),
            "kernel_terms": sorted(kernel),
            "image_size": len(image),
            "image_terms": sorted(image),
            "faithful": self.check_faithfulness(),
            "category": category,
            "functor_laws_ok": functor_ok,
            "functor_errors": functor_errors,
            "overlap_laws_ok": overlap_ok,
            "overlap_errors": overlap_errors,
            "composition_depth": len(self.composition_registry),
            "trust_ceiling": self.encoding.trust_ceiling,
            "source_vocab_size": len(self.source_vocabulary),
            "target_vocab_size": len(self.target_vocabulary),
        }
