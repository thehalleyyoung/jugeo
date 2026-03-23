#!/usr/bin/env python3
"""Comprehensive verification script for Python files."""

import os
import ast
from pathlib import Path

# List of files to check
FILES = [
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/ir_stack/s01_the_theory_wants_a_small_number_of.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/ir_stack/s02_normal_forms_where_comparison_cach.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/ir_stack/s03_an_implementation_ready_theory_nee.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/ir_stack/s04_lowering_should_preserve_ambiguity.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/text_encodings/s01_why_text_deserves_its_own_structur.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/text_encodings/s02_the_normalized_text_environment.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/text_encodings/s03_encoding_families.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/text_encodings/s04_countermodels_and_clausewise_expla.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/tensor_quantifier_encodings/s01_why_this_family_matters_disproport.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/tensor_quantifier_encodings/s02_affine_and_quasi_affine_normal_for.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/tensor_quantifier_encodings/s04_witness_extraction_and_proof_burde.py",
    "/Users/halleyyoung/Documents/jugeo/src/jugeo/encodings/scalar_encodings/s03_exact_failure_artifacts.py",
]

def check_file_syntax(filepath):
    """Check if a Python file has valid syntax."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            ast.parse(f.read())
        return "✓ Valid"
    except SyntaxError as e:
        return f"✗ SyntaxError: {e.msg}"
    except Exception as e:
        return f"✗ Error: {type(e).__name__}"

def get_file_size_kb(filepath):
    """Get file size in KB."""
    try:
        size_bytes = os.path.getsize(filepath)
        return round(size_bytes / 1024, 2)
    except OSError:
        return None

def main():
    print("\n" + "="*120)
    print("PYTHON FILES VERIFICATION REPORT")
    print("="*120 + "\n")
    
    # Print header
    print(f"{'No.':<5} {'Exists':<8} {'Size (KB)':<12} {'≥15KB':<8} {'Syntax Status':<80}")
    print("-" * 120)
    
    results = []
    
    for idx, filepath in enumerate(FILES, 1):
        exists = os.path.exists(filepath)
        exists_str = "✓ Yes" if exists else "✗ No"
        
        if exists:
            size_kb = get_file_size_kb(filepath)
            size_str = f"{size_kb}" if size_kb is not None else "N/A"
            gte_15kb = "✓ Yes" if size_kb and size_kb >= 15 else "✗ No"
            syntax = check_file_syntax(filepath)
        else:
            size_str = "N/A"
            gte_15kb = "N/A"
            syntax = "N/A (file not found)"
        
        print(f"{idx:<5} {exists_str:<8} {size_str:<12} {gte_15kb:<8} {syntax:<80}")
        results.append({
            "no": idx,
            "exists": exists,
            "size_kb": size_kb if exists else None,
            "gte_15kb": size_kb >= 15 if exists and size_kb else False,
            "syntax": syntax
        })
    
    print("-" * 120)
    
    # Summary statistics
    total_files = len(FILES)
    existing_files = sum(1 for r in results if r["exists"])
    large_files = sum(1 for r in results if r["gte_15kb"])
    valid_syntax = sum(1 for r in results if "✓ Valid" in r["syntax"])
    
    print(f"\nSUMMARY:")
    print(f"  Total files checked:     {total_files}")
    print(f"  Files exist:             {existing_files}/{total_files}")
    print(f"  Files ≥ 15KB:            {large_files}/{total_files}")
    print(f"  Valid Python syntax:     {valid_syntax}/{existing_files}")
    print("\n" + "="*120 + "\n")

if __name__ == "__main__":
    main()
