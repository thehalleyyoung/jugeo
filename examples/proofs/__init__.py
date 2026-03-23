"""examples/proofs — Proof-carrying Python examples for JuGeo.

Each module in this package demonstrates a different verification scenario:

    01_spec_satisfaction.py     Prove a program meets a specification on a cover
    02_equivalence.py           Prove two implementations are equivalent
    03_bug_detection.py         Detect Python bugs as structured obstructions
    04_refinement_types.py      Verify refinement-typed functions
    05_effects_exceptions.py    Verify exception safety as sheaf sections
    06_context_managers.py      Verify resource safety via covering families
    07_proof_carrying_sort.py   A complete proof-carrying sorting algorithm

Run any example:
    python -m examples.proofs.01_spec_satisfaction

Run all examples:
    python -m pytest examples/proofs/ -v
"""
