#!/usr/bin/env python3
"""Analysis script for Python code blocks in LaTeX papers 26-50"""
import re
from pathlib import Path

def count_python_blocks(filepath):
    """Count Python lstlisting blocks in a LaTeX file"""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Pattern 1: \begin{lstlisting}[style=jugeo-python
        pattern1 = r'\\begin\s*\{\s*lstlisting\s*\}[^}]*jugeo-python'
        
        # Pattern 2: \begin{lstlisting}[...language=python...
        pattern2 = r'\\begin\s*\{\s*lstlisting\s*\}[^}]*language\s*=\s*[{]?[Pp]ython'
        
        # Count occurrences
        count1 = len(re.findall(pattern1, content, re.IGNORECASE | re.DOTALL))
        count2 = len(re.findall(pattern2, content, re.IGNORECASE | re.DOTALL))
        
        # Return the total unique count
        total = count1 + count2
        
        return total, content
    except Exception as e:
        return 0, None

def main():
    papers_dir = Path("/Users/halleyyoung/Documents/jugeo/papers")
    results = []
    
    for paper_num in range(26, 51):
        # Find the file
        pattern = f"paper{paper_num:02d}-*.tex"
        matches = sorted(papers_dir.glob(pattern))
        
        if not matches:
            results.append({
                'num': paper_num,
                'file': 'NOT FOUND',
                'count': 0,
                'status': 'FAIL'
            })
            continue
        
        filepath = matches[0]
        filename = filepath.name
        count, content = count_python_blocks(filepath)
        
        status = 'PASS' if count >= 2 else 'FAIL'
        results.append({
            'num': paper_num,
            'file': filename,
            'count': count,
            'status': status
        })
    
    # Write results to file
    output_file = Path("/tmp/python_blocks_analysis.txt")
    with open(output_file, 'w') as f:
        f.write("="*90 + "\n")
        f.write("PYTHON CODE BLOCKS ANALYSIS: Papers 26-50\n")
        f.write("="*90 + "\n\n")
        
        f.write(f"{'Paper':<10} {'Filename':<45} {'Python Blocks':<15} {'Status':<8}\n")
        f.write("-"*90 + "\n")
        
        for r in results:
            paper_str = f"Paper {r['num']}"
            f.write(f"{paper_str:<10} {r['file']:<45} {str(r['count']):<15} {r['status']:<8}\n")
        
        f.write("\n" + "-"*90 + "\n")
        f.write("SUMMARY:\n")
        
        pass_count = sum(1 for r in results if r['status'] == 'PASS')
        fail_count = sum(1 for r in results if r['status'] == 'FAIL')
        total = len(results)
        
        f.write(f"  PASS (>= 2 blocks): {pass_count} papers\n")
        f.write(f"  FAIL (< 2 blocks):  {fail_count} papers\n")
        f.write(f"  TOTAL:              {total} papers\n")
        if total > 0:
            f.write(f"  Pass Rate:          {100 * pass_count // total}% ({pass_count}/{total})\n")
        f.write("="*90 + "\n")
    
    # Also print to stdout
    print("Analysis complete! Results saved to /tmp/python_blocks_analysis.txt")
    print("\nResults:")
    for r in results:
        paper_str = f"Paper {r['num']}"
        print(f"{paper_str:<10} {r['file']:<45} {str(r['count']):<15} {r['status']:<8}")

if __name__ == '__main__':
    main()
